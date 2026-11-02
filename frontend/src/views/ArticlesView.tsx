import React, { useState, useEffect } from 'react';
import { Article, MacroBreakdownResponse } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { fetchArticles } from '../services/api';
import { ArticleCard } from '../components/ArticleCard';
import { ArticleLeftDrawer } from '../components/ArticleLeftDrawer';
import {
  BookOpen,
  Filter,
  Search,
  Sparkles,
  Zap,
  TrendingUp,
  Layers,
} from 'lucide-react';

interface ArticlesViewProps {
  onHandoffToTool: (breakdown: MacroBreakdownResponse, article: Article) => void;
  onNavigateTab: (tab: 'home' | 'articles' | 'tool' | 'admin') => void;
}

export const ArticlesView: React.FC<ArticlesViewProps> = ({ onHandoffToTool, onNavigateTab }) => {
  const { language, t } = useLanguage();
  const [articles, setArticles] = useState<Article[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTag, setSelectedTag] = useState<string>('all');
  const [selectedArticleForDrawer, setSelectedArticleForDrawer] = useState<Article | null>(null);

  useEffect(() => {
    fetchArticles(20, 0)
      .then((data) => setArticles(data))
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  }, []);

  const filteredArticles = articles.filter((art) => {
    const matchesSearch =
      art.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      art.chapeau.toLowerCase().includes(searchQuery.toLowerCase()) ||
      art.content.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesTag =
      selectedTag === 'all'
        ? true
        : art.domain_tag?.toLowerCase().includes(selectedTag) ||
          art.title.toLowerCase().includes(selectedTag);

    return matchesSearch && matchesTag;
  });

  const featuredArticle = filteredArticles[0];
  const gridArticles = filteredArticles.slice(1);

  return (
    <div className="space-y-8 pb-16 font-sans">
      
      {/* Newspaper Masthead Banner (Trình bày giống trang báo điện tử) */}
      <section className="bg-white border-b border-gray-200 py-8">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 border-b border-gray-100 pb-6">
            <div>
              <div className="flex items-center space-x-2 text-brand-accent text-xs font-mono uppercase tracking-widest font-bold mb-1">
                <BookOpen className="w-3.5 h-3.5" />
                <span>PHÒNG BÁO CHÍ &amp; GIẢI MÃ CHÍNH SÁCH</span>
              </div>
              <h1 className="font-serif text-3xl sm:text-4xl md:text-5xl font-extrabold text-brand-dark tracking-tight">
                {t('sec2_title')}
              </h1>
              <p className="font-serif text-sm sm:text-base text-gray-600 mt-2 max-w-2xl">
                {t('sec2_desc')}
              </p>
            </div>

            {/* Feature callout */}
            <div className="bg-brand-subtle border border-brand-accent/20 p-3 rounded-xs max-w-md shrink-0 text-xs font-mono">
              <div className="flex items-center gap-1.5 text-brand-accent font-bold mb-1">
                <Zap className="w-3.5 h-3.5" />
                <span>Tính Năng Độc Quyền:</span>
              </div>
              <p className="text-gray-700 text-[11px] leading-relaxed">
                Mỗi bài báo tích hợp nút phân tích AI, mở cửa sổ kéo mở rộng ở nửa bên trái màn hình để bóc tách tác động chi tiêu và chuyển dữ liệu sang Tool!
              </p>
            </div>
          </div>

          {/* Search & Topic Filters */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pt-6">
            {/* Filter pills */}
            <div className="flex flex-wrap gap-2 text-xs font-mono">
              <button
                type="button"
                onClick={() => setSelectedTag('all')}
                className={`px-3 py-1.5 border rounded-xs transition-colors ${
                  selectedTag === 'all'
                    ? 'bg-brand-accent text-white border-brand-accent font-bold'
                    : 'bg-gray-50 text-gray-700 border-gray-200 hover:border-gray-400'
                }`}
              >
                {t('sec2_filter_all')}
              </button>
              <button
                type="button"
                onClick={() => setSelectedTag('lạm phát')}
                className={`px-3 py-1.5 border rounded-xs transition-colors ${
                  selectedTag === 'lạm phát'
                    ? 'bg-brand-accent text-white border-brand-accent font-bold'
                    : 'bg-gray-50 text-gray-700 border-gray-200 hover:border-gray-400'
                }`}
              >
                {t('sec2_filter_macro')}
              </button>
              <button
                type="button"
                onClick={() => setSelectedTag('xăng')}
                className={`px-3 py-1.5 border rounded-xs transition-colors ${
                  selectedTag === 'xăng'
                    ? 'bg-brand-accent text-white border-brand-accent font-bold'
                    : 'bg-gray-50 text-gray-700 border-gray-200 hover:border-gray-400'
                }`}
              >
                {t('sec2_filter_energy')}
              </button>
              <button
                type="button"
                onClick={() => setSelectedTag('tỷ giá')}
                className={`px-3 py-1.5 border rounded-xs transition-colors ${
                  selectedTag === 'tỷ giá'
                    ? 'bg-brand-accent text-white border-brand-accent font-bold'
                    : 'bg-gray-50 text-gray-700 border-gray-200 hover:border-gray-400'
                }`}
              >
                {t('sec2_filter_fx')}
              </button>
            </div>

            {/* Search Input */}
            <div className="relative w-full sm:w-72">
              <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Tìm bài viết vĩ mô..."
                className="w-full pl-9 pr-3 py-1.5 text-xs font-mono border border-gray-300 rounded-xs focus:border-brand-accent outline-none"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Main Articles Newspaper Grid */}
      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {loading && (
          <div className="py-20 text-center font-mono text-xs text-gray-500">
            Đang tải kho bài viết phân tích kinh tế vĩ mô...
          </div>
        )}

        {!loading && filteredArticles.length === 0 && (
          <div className="p-12 text-center bg-gray-50 border border-gray-200 font-mono text-xs text-gray-400">
            Không tìm thấy bài viết nào phù hợp với bộ lọc.
          </div>
        )}

        {!loading && featuredArticle && (
          <>
            {/* Featured Lead Story (Trình bày giống trang nhất báo điện tử) */}
            <ArticleCard
              article={featuredArticle}
              featured={true}
              onAnalyze={(art) => setSelectedArticleForDrawer(art)}
            />

            {/* 3-Column Grid for Secondary Stories */}
            {gridArticles.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {gridArticles.map((article) => (
                  <ArticleCard
                    key={article.id}
                    article={article}
                    onAnalyze={(art) => setSelectedArticleForDrawer(art)}
                  />
                ))}
              </div>
            )}
          </>
        )}
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
