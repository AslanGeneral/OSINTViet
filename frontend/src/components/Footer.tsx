import React from 'react';
import { useLanguage } from '../context/LanguageContext';
import { useSiteConfig } from '../context/SiteConfigContext';
import { Shield, GitBranch, Cpu, Award } from 'lucide-react';

export const Footer: React.FC = () => {
  const { language, t } = useLanguage();
  const { config } = useSiteConfig();

  return (
    <footer className="bg-brand-dark text-white border-t border-gray-800 mt-auto">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-8 pb-8 border-b border-gray-800">
          
          <div className="md:col-span-2 space-y-3">
            <div className="flex items-center space-x-2 text-red-400 font-mono text-xs uppercase tracking-widest font-bold">
              <Shield className="w-4 h-4" />
              <span>{config.site_title || 'OSINT Monitor'}</span>
            </div>
            <p className="font-serif text-sm text-gray-300 leading-relaxed max-w-lg">
              {language === 'vi' ? config.project_description : config.project_description_en}
            </p>
            <div className="text-xs font-mono text-gray-400 pt-2 flex items-center space-x-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400 status-pulse-green"></span>
              <span>Neuro-Symbolic Pipeline V2 (Macro → Micro → Dual)</span>
            </div>
          </div>

          <div className="space-y-3">
            <h4 className="font-mono text-xs uppercase tracking-wider text-gray-400 font-bold">
              {language === 'vi' ? 'Tiêu Chuẩn Định Lượng' : 'Quantitative Specs'}
            </h4>
            <ul className="text-xs font-mono space-y-2 text-gray-300">
              <li>• SPEC-MACRO-QUANT v5.7</li>
              <li>• QUAIDS &amp; Stone-Geary LES</li>
              <li>• Carroll-Toche Buffer Stock EGM</li>
              <li>• Two-Asset HANK Downscaling</li>
              <li>• RFC 8785 Canonical JCS</li>
            </ul>
          </div>

          <div className="space-y-3">
            <h4 className="font-mono text-xs uppercase tracking-wider text-gray-400 font-bold">
              {language === 'vi' ? 'Nguồn Dữ Liệu' : 'Data Sources'}
            </h4>
            <ul className="text-xs font-mono space-y-2 text-gray-300">
              <li>• TradingEconomics API/Scraper</li>
              <li>• World Bank 36-Series Panel</li>
              <li>• Tổng cục Thống kê (GSO Vietnam)</li>
              <li>• Petrolimex Bán lẻ Xăng dầu</li>
              <li>• Thị trường Chứng khoán Toàn cầu</li>
            </ul>
          </div>

        </div>

        <div className="flex flex-col sm:flex-row justify-between items-center text-xs font-mono text-gray-500 gap-4">
          <div>
            © 2026 {config.site_title || 'OSINT Monitor'} — Sáng kiến Bảo vệ Ngân sách Dân sinh Nguồn mở.
          </div>
          <div className="flex items-center space-x-4">
            <span className="hover:text-gray-400 transition-colors">Apache 2.0 / MIT Open Access</span>
            <span>•</span>
            <span className="hover:text-gray-400 transition-colors">ProbLog Verification Active</span>
          </div>
        </div>
      </div>
    </footer>
  );
};
