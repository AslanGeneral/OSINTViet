import React, { useState, useEffect } from 'react';
import { Article, MacroBreakdownResponse } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { useSiteConfig } from '../context/SiteConfigContext';
import { fetchArticles } from '../services/api';
import { ArticleCard } from '../components/ArticleCard';
import { ArticleLeftDrawer } from '../components/ArticleLeftDrawer';
import {
  Shield,
  Zap,
  ArrowRight,
  TrendingUp,
  Cpu,
  BookOpen,
  Terminal,
  Activity,
  Layers,
  Sparkles,
} from 'lucide-react';

interface HomeViewProps {
  onNavigateTab: (tab: 'home' | 'articles' | 'tool' | 'admin') => void;
  onHandoffToTool: (breakdown: MacroBreakdownResponse, article: Article) => void;
}

export const HomeView: React.FC<HomeViewProps> = ({ onNavigateTab, onHandoffToTool }) => {
  const { language, t } = useLanguage();
  const { config } = useSiteConfig();
  const [latestArticles, setLatestArticles] = useState<Article[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedArticleForDrawer, setSelectedArticleForDrawer] = useState<Article | null>(null);

  useEffect(() => {
    fetchArticles(4, 0)
      .then((data) => {
        // Explicit prompt requirement: 3-4 articles
        setLatestArticles(data.slice(0, 4));
      })
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-12 pb-16 font-sans">
      
      {/* Editorial Hero Statement (Section I - Trang chủ) */}
      <section className="bg-white border-b border-gray-200 py-12 md:py-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="max-w-4xl space-y-6">
            
            {/* Kicker badge */}
            <div className="inline-flex items-center space-x-2 text-brand-accent text-xs font-mono uppercase tracking-widest font-bold bg-brand-subtle px-3 py-1 border border-brand-accent/20">
              <Sparkles className="w-3.5 h-3.5" />
              <span>{t('sec1_badge')}</span>
            </div>

            {/* Project Title & Main Headline */}
            <h1 className="font-serif text-3xl sm:text-5xl md:text-6xl font-extrabold text-brand-dark tracking-tight leading-[1.15]">
              {config.site_title || 'OSINT Monitor'}
            </h1>

            {/* Project Description (Required exact text from prompt) */}
            <div className="border-l-4 border-brand-accent pl-5 py-1">
              <span className="text-[11px] font-mono uppercase tracking-wider text-gray-500 font-bold block mb-1">
                {t('sec1_project_desc_title')}:
              </span>
              <p className="font-serif text-lg sm:text-2xl text-brand-dark font-medium leading-snug">
                "{language === 'vi' ? config.project_description : config.project_description_en}"
              </p>
            </div>

            {/* Quick Action Navigation CTAs */}
            <div className="flex flex-wrap items-center gap-4 pt-4">
              <button
                type="button"
                onClick={() => onNavigateTab('tool')}
                className="px-6 py-3 bg-brand-accent hover:bg-brand-accent-hover text-white text-xs sm:text-sm font-mono font-bold tracking-wider uppercase transition-all shadow-sm flex items-center gap-2"
              >
                <Terminal className="w-4 h-4" />
                <span>{t('sec1_open_tool_cta')}</span>
              </button>

              <button
                type="button"
                onClick={() => onNavigateTab('articles')}
                className="px-6 py-3 bg-gray-100 hover:bg-gray-200 text-brand-dark text-xs sm:text-sm font-mono font-semibold tracking-wider uppercase transition-all flex items-center gap-2 border border-gray-300"
              >
                <BookOpen className="w-4 h-4 text-brand-accent" />
                <span>{t('sec1_explore_more')}</span>
              </button>
            </div>

          </div>
        </div>
      </section>

      {/* Section I.2: Latest Articles Block (3-4 bài viết gần nhất, nếu trống để null) */}
      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="border-b border-gray-200 pb-4 mb-6 flex flex-col sm:flex-row sm:items-end justify-between gap-2">
          <div>
            <span className="text-xs font-mono uppercase tracking-widest text-brand-accent font-bold">
              EDITORIAL DISPATCHES
            </span>
            <h2 className="font-serif text-2xl sm:text-3xl font-bold text-brand-dark mt-1">
              {t('sec1_latest_articles_title')}
            </h2>
          </div>
          <button
            onClick={() => onNavigateTab('articles')}
            className="text-xs font-mono font-bold text-brand-accent hover:underline flex items-center gap-1"
          >
            <span>{t('sec1_explore_more')}</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Loading state */}
        {loading && (
          <div className="py-12 text-center font-mono text-xs text-gray-500">
            Đang tải 3-4 bài viết phân tích mới nhất từ cơ sở dữ liệu...
          </div>
        )}

        {/* Empty state (renders null as requested) */}
        {!loading && latestArticles.length === 0 && (
          <div className="p-8 border border-gray-200 bg-gray-50 text-center font-mono text-xs text-gray-400">
            {t('sec1_empty_articles')}
          </div>
        )}

        {/* Latest 3-4 Articles Grid */}
        {!loading && latestArticles.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {latestArticles.map((article) => (
              <ArticleCard
                key={article.id}
                article={article}
                onAnalyze={(art) => setSelectedArticleForDrawer(art)}
              />
            ))}
          </div>
        )}
      </section>

      {/* Feature Pillar Highlights */}
      <section className="bg-gray-50 border-y border-gray-200 py-12">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            
            <div className="bg-white border border-gray-200 p-6 space-y-3 shadow-2xs">
              <div className="w-10 h-10 bg-brand-subtle text-brand-accent flex items-center justify-center rounded font-serif font-bold text-lg">
                I
              </div>
              <h3 className="font-serif text-lg font-bold text-brand-dark">
                Giải Mã Vĩ Mô Dễ Hiểu
              </h3>
              <p className="text-xs font-serif text-gray-600 leading-relaxed">
                Chuyển hóa các bài báo và báo cáo chính sách phức tạp thành ngôn ngữ cấp 2 dễ hiểu, giải thích tác động cụ thể đến bữa ăn, tiền xăng và học phí.
              </p>
            </div>

            <div className="bg-white border border-gray-200 p-6 space-y-3 shadow-2xs">
              <div className="w-10 h-10 bg-blue-50 text-blue-700 flex items-center justify-center rounded font-serif font-bold text-lg">
                II
              </div>
              <h3 className="font-serif text-lg font-bold text-brand-dark">
                Parameters &amp; Headless Scraper
              </h3>
              <p className="text-xs font-serif text-gray-600 leading-relaxed">
                Tích hợp dữ liệu thị trường 4 quốc gia (Mỹ, Anh, TQ, Việt Nam) và chỉ số từ TradingEconomics thông qua trình duyệt headless Playwright.
              </p>
            </div>

            <div className="bg-white border border-gray-200 p-6 space-y-3 shadow-2xs">
              <div className="w-10 h-10 bg-purple-50 text-purple-700 flex items-center justify-center rounded font-serif font-bold text-lg">
                III
              </div>
              <h3 className="font-serif text-lg font-bold text-brand-dark">
                Studio VS Code &amp; Pipeline V2
              </h3>
              <p className="text-xs font-serif text-gray-600 leading-relaxed">
                Mô phỏng 7 tầng Pipeline V2, hỗ trợ 3 chế độ Macro, Micro và Dual-mode, cho phép người dùng trò chuyện và kiểm chứng logic hình thức HMAC.
              </p>
            </div>

          </div>
        </div>
      </section>

      {/* Resizable Left Drawer for Single-Article AI Breakdown */}
      <ArticleLeftDrawer
        article={selectedArticleForDrawer}
        isOpen={!!selectedArticleForDrawer}
        onClose={() => setSelectedArticleForDrawer(null)}
        onHandoffToTool={(breakdown, article) => {
          setSelectedArticleForDrawer(null);
          onHandoffToTool(breakdown, article);
          onNavigateTab('tool');
        }}
      />

    </div>
  );
};
