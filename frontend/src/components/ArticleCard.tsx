import React from 'react';
import { Article } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { Zap, Clock, ExternalLink } from 'lucide-react';

interface ArticleCardProps {
  article: Article;
  onAnalyze: (article: Article) => void;
  featured?: boolean;
}

export const ArticleCard: React.FC<ArticleCardProps> = ({ article, onAnalyze, featured = false }) => {
  const { language, t } = useLanguage();

  const formattedDate = article.ingested_at
    ? new Date(article.ingested_at).toLocaleDateString(language === 'vi' ? 'vi-VN' : 'en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : '';

  if (featured) {
    return (
      <article className="group grid grid-cols-1 lg:grid-cols-12 gap-6 bg-white border border-gray-200 p-6 md:p-8 hover:shadow-editorial transition-all mb-8">
        <div className="lg:col-span-7 flex flex-col justify-between order-2 lg:order-1">
          <div>
            <div className="flex items-center space-x-3 text-xs font-mono uppercase tracking-wider mb-2">
              <span className="text-brand-accent font-bold">
                {article.domain_tag || 'KINH TẾ VĨ MÔ'}
              </span>
              <span className="text-gray-400">•</span>
              <span className="text-gray-500">{article.source}</span>
            </div>

            <h2 className="font-serif text-2xl sm:text-3xl font-bold text-brand-dark group-hover:text-brand-accent transition-colors leading-tight mb-4">
              {article.title}
            </h2>

            <p className="font-serif text-gray-700 text-sm sm:text-base leading-relaxed line-clamp-3 mb-6">
              {article.chapeau}
            </p>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-4 pt-4 border-t border-gray-100">
            <div className="flex items-center text-xs font-mono text-gray-500 space-x-3">
              <span className="flex items-center">
                <Clock className="w-3.5 h-3.5 mr-1" />
                5 phút đọc
              </span>
              {formattedDate && <span>• {formattedDate}</span>}
            </div>

            <div className="flex items-center space-x-3">
              {article.url && (
                <a
                  href={article.url}
                  target="_blank"
                  rel="noreferrer"
                  className="p-2 text-gray-500 hover:text-brand-dark hover:bg-gray-100 transition-colors"
                  title="Mở bài viết gốc"
                >
                  <ExternalLink className="w-4 h-4" />
                </a>
              )}
              <button
                type="button"
                onClick={() => onAnalyze(article)}
                className="inline-flex items-center px-4 py-2 bg-brand-accent hover:bg-brand-accent-hover text-white text-xs font-mono font-bold tracking-wider uppercase transition-colors shadow-2xs"
              >
                <Zap className="w-3.5 h-3.5 mr-1.5" />
                {t('sec2_btn_ai_breakdown')}
              </button>
            </div>
          </div>
        </div>

        <div className="lg:col-span-5 order-1 lg:order-2 overflow-hidden bg-gray-100 border border-gray-200 aspect-[16/10] lg:aspect-auto">
          {article.thumbnail ? (
            <img
              src={article.thumbnail}
              alt={article.title}
              className="w-full h-full object-cover group-hover:scale-102 transition-transform duration-300"
              loading="lazy"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center font-mono text-xs text-gray-400 bg-gray-50">
              null
            </div>
          )}
        </div>
      </article>
    );
  }

  return (
    <article className="group flex flex-col justify-between bg-white border border-gray-200 p-5 hover:shadow-editorial transition-all">
      <div>
        <div className="aspect-[16/10] overflow-hidden bg-gray-100 border border-gray-200 mb-4">
          {article.thumbnail ? (
            <img
              src={article.thumbnail}
              alt={article.title}
              className="w-full h-full object-cover group-hover:scale-102 transition-transform duration-300"
              loading="lazy"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center font-mono text-xs text-gray-400 bg-gray-50">
              null
            </div>
          )}
        </div>

        <div className="flex items-center space-x-2 text-[10px] font-mono uppercase tracking-wider text-gray-500 mb-2">
          <span className="text-brand-accent font-bold">
            {article.domain_tag || 'VĨ MÔ'}
          </span>
          <span>•</span>
          <span className="truncate">{article.source}</span>
        </div>

        <h3 className="font-serif text-lg font-bold text-brand-dark group-hover:text-brand-accent transition-colors leading-snug mb-2 line-clamp-2">
          {article.title}
        </h3>

        <p className="font-serif text-xs text-gray-600 leading-relaxed line-clamp-3 mb-4">
          {article.chapeau}
        </p>
      </div>

      <div className="pt-3 border-t border-gray-100 flex items-center justify-between mt-auto">
        <span className="text-[11px] font-mono text-gray-400">
          {formattedDate || '4 min read'}
        </span>

        <button
          type="button"
          onClick={() => onAnalyze(article)}
          className="inline-flex items-center px-3 py-1.5 bg-gray-100 hover:bg-brand-accent hover:text-white text-brand-dark text-[11px] font-mono font-bold tracking-wider uppercase transition-colors"
        >
          <Zap className="w-3 h-3 mr-1 text-brand-accent group-hover:text-white" />
          {language === 'vi' ? 'Breakdown AI' : 'AI Breakdown'}
        </button>
      </div>
    </article>
  );
};
