import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { SiteConfig } from '../types';
import { fetchSiteConfig, updateSiteConfig as apiUpdateSiteConfig } from '../services/api';

interface SiteConfigContextType {
  config: SiteConfig;
  updateConfig: (newCfg: Partial<SiteConfig>) => Promise<void>;
  loading: boolean;
}

const defaultConfig: SiteConfig = {
  site_title: 'OSINT Monitor',
  project_name: 'OSINT Monitor',
  tagline: 'Open-Source Quantitative Intelligence Platform',
  project_description: 'Là công cụ định lượng nguồn mở, cung cấp giải pháp thân thiện và dễ hiểu cho người dùng cá nhân, với sự hỗ trợ của Trí tuệ Nhân tạo',
  project_description_en: 'An open-source quantitative tool providing user-friendly, accessible solutions for individual households, powered by Artificial Intelligence',
  announcement: 'Bản tin Vĩ mô: Theo dõi sát sao biến động lạm phát, giá nhiên liệu và tác động truyền dẫn đến ngân sách hộ gia đình năm 2026.',
  announcement_en: 'Macro Bulletin: Closely tracking inflation trends, energy price shocks, and transmission effects on household budgets in 2026.',
  auto_fetch_interval_hours: 6,
  enable_live_scraper: true,
  enable_dual_mode: true,
  enable_dark_mode: true,
  show_ticker: true,
};

const SiteConfigContext = createContext<SiteConfigContextType | undefined>(undefined);

export const SiteConfigProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [config, setConfig] = useState<SiteConfig>(defaultConfig);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchSiteConfig()
      .then((cfg) => {
        if (cfg) setConfig(cfg);
      })
      .catch((err) => console.warn('Could not load site config:', err))
      .finally(() => setLoading(false));
  }, []);

  const updateConfig = async (newCfg: Partial<SiteConfig>) => {
    try {
      const res = await apiUpdateSiteConfig(newCfg);
      if (res && res.config) {
        setConfig(res.config);
      } else {
        setConfig((prev) => ({ ...prev, ...newCfg }));
      }
    } catch (err) {
      console.warn('Update site config fallback to local state:', err);
      setConfig((prev) => ({ ...prev, ...newCfg }));
    }
  };

  return (
    <SiteConfigContext.Provider value={{ config, updateConfig, loading }}>
      {children}
    </SiteConfigContext.Provider>
  );
};

export const useSiteConfig = (): SiteConfigContextType => {
  const context = useContext(SiteConfigContext);
  if (!context) {
    throw new Error('useSiteConfig must be used within a SiteConfigProvider');
  }
  return context;
};
