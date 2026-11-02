import React from 'react';
import { useLanguage } from '../context/LanguageContext';
import { useSiteConfig } from '../context/SiteConfigContext';
import { Globe, Lock, Shield, Cpu, Activity, BarChart2, BookOpen, Terminal } from 'lucide-react';

interface HeaderProps {
  currentTab: 'home' | 'articles' | 'tool' | 'admin';
  setCurrentTab: (tab: 'home' | 'articles' | 'tool' | 'admin') => void;
}

export const Header: React.FC<HeaderProps> = ({ currentTab, setCurrentTab }) => {
  const { language, setLanguage, t } = useLanguage();
  const { config } = useSiteConfig();

  const currentDate = new Date().toLocaleDateString(language === 'vi' ? 'vi-VN' : 'en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <header className="border-b border-gray-200 bg-white sticky top-0 z-40 shadow-xs">
      {/* Top Utility & Ticker Bar */}
      {config.show_ticker && (
        <div className="bg-brand-dark text-white text-[11px] font-mono py-1.5 px-4 border-b border-gray-800">
          <div className="max-w-7xl mx-auto flex flex-col sm:flex-row justify-between items-center gap-2">
            <div className="flex items-center space-x-3 overflow-hidden">
              <span className="inline-flex items-center text-red-400 font-bold tracking-wider uppercase text-[10px] bg-red-950/80 px-2 py-0.5 rounded border border-red-800/60">
                <Activity className="w-3 h-3 mr-1 animate-pulse" />
                {language === 'vi' ? 'TRỰC TIẾP' : 'LIVE TICKER'}
              </span>
              <span className="truncate text-gray-300">
                {language === 'vi' ? config.announcement : config.announcement_en || config.announcement}
              </span>
            </div>

            <div className="flex items-center space-x-4 shrink-0 text-gray-400 text-[10px]">
              <span className="hidden md:inline">{currentDate}</span>
              <span className="inline-flex items-center text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1.5 status-pulse-green"></span>
                HMAC-SHA256 0% Phỏng Đoán
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Main Masthead (Irregular Warfare Editorial Style) */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 sm:py-6">
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 border-b border-gray-100 pb-4">
          
          {/* Brand Logo & Editorial Kicker */}
          <div className="cursor-pointer group" onClick={() => setCurrentTab('home')}>
            <div className="flex items-center space-x-2 text-brand-accent text-[11px] font-mono tracking-widest uppercase font-semibold mb-1">
              <Shield className="w-3.5 h-3.5" />
              <span>{t('brand_kicker')}</span>
            </div>
            <div className="flex items-baseline space-x-3">
              <h1 className="font-serif text-3xl sm:text-4xl md:text-5xl font-extrabold tracking-tight text-brand-dark group-hover:text-brand-accent transition-colors">
                {config.site_title || 'OSINT Monitor'}
              </h1>
              <span className="text-xs font-mono text-gray-500 uppercase tracking-wider hidden sm:inline">
                v2.0 Neuro-Symbolic
              </span>
            </div>
            <p className="font-serif italic text-xs sm:text-sm text-gray-600 mt-1 max-w-xl">
              {config.tagline || t('brand_tagline')}
            </p>
          </div>

          {/* Right Header Actions: Language Switch & Admin Quick Link */}
          <div className="flex items-center space-x-3 self-end md:self-auto">
            {/* Language Switch Toggle */}
            <div className="inline-flex items-center p-0.5 rounded-sm bg-gray-100 border border-gray-300 text-xs font-mono">
              <button
                type="button"
                onClick={() => setLanguage('vi')}
                className={`px-2.5 py-1 rounded-xs transition-all ${
                  language === 'vi'
                    ? 'bg-brand-accent text-white font-bold shadow-2xs'
                    : 'text-gray-600 hover:text-brand-dark'
                }`}
                title="Tiếng Việt"
              >
                🇻🇳 Tiếng Việt
              </button>
              <button
                type="button"
                onClick={() => setLanguage('en')}
                className={`px-2.5 py-1 rounded-xs transition-all ${
                  language === 'en'
                    ? 'bg-brand-accent text-white font-bold shadow-2xs'
                    : 'text-gray-600 hover:text-brand-dark'
                }`}
                title="English"
              >
                🇬🇧 English
              </button>
            </div>

            {/* Admin Portal Pill Button */}
            <button
              onClick={() => setCurrentTab('admin')}
              className={`inline-flex items-center px-3 py-1 text-xs font-mono rounded-xs border transition-colors ${
                currentTab === 'admin'
                  ? 'bg-brand-dark text-white border-brand-dark font-bold'
                  : 'bg-white text-gray-700 border-gray-300 hover:border-brand-accent hover:text-brand-accent'
              }`}
              title="Cổng Quản Trị Hệ Thống"
            >
              <Lock className="w-3 h-3 mr-1.5" />
              <span>Admin</span>
            </button>
          </div>

        </div>

        {/* Primary Editorial Navigation Bar */}
        <nav className="flex items-center space-x-1 sm:space-x-8 pt-3 overflow-x-auto text-xs sm:text-sm font-medium tracking-wide">
          <button
            onClick={() => setCurrentTab('home')}
            className={`pb-2 border-b-2 font-serif transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              currentTab === 'home'
                ? 'border-brand-accent text-brand-accent font-bold'
                : 'border-transparent text-gray-700 hover:text-brand-accent hover:border-gray-300'
            }`}
          >
            <BookOpen className="w-4 h-4" />
            {t('nav_home')}
          </button>

          <button
            onClick={() => setCurrentTab('articles')}
            className={`pb-2 border-b-2 font-serif transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              currentTab === 'articles'
                ? 'border-brand-accent text-brand-accent font-bold'
                : 'border-transparent text-gray-700 hover:text-brand-accent hover:border-gray-300'
            }`}
          >
            <BarChart2 className="w-4 h-4" />
            {t('nav_articles')}
          </button>

          <button
            onClick={() => setCurrentTab('tool')}
            className={`pb-2 border-b-2 font-serif transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              currentTab === 'tool'
                ? 'border-brand-accent text-brand-accent font-bold'
                : 'border-transparent text-gray-700 hover:text-brand-accent hover:border-gray-300'
            }`}
          >
            <Terminal className="w-4 h-4 text-emerald-600" />
            {t('nav_tool')}
            <span className="font-mono text-[10px] bg-red-100 text-brand-accent px-1.5 py-0.2 rounded font-bold ml-1">
              VS Code
            </span>
          </button>

          <button
            onClick={() => setCurrentTab('admin')}
            className={`pb-2 border-b-2 font-serif transition-colors whitespace-nowrap flex items-center gap-1.5 ${
              currentTab === 'admin'
                ? 'border-brand-accent text-brand-accent font-bold'
                : 'border-transparent text-gray-700 hover:text-brand-accent hover:border-gray-300'
            }`}
          >
            <Shield className="w-4 h-4 text-gray-400" />
            {t('nav_admin')}
          </button>
        </nav>
      </div>
    </header>
  );
};
