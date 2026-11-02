import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Article, MacroBreakdownResponse } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { analyzeArticleBreakdown } from '../services/api';
import {
  X,
  Maximize2,
  Minimize2,
  Zap,
  ArrowRight,
  TrendingUp,
  TrendingDown,
  CheckCircle2,
  AlertCircle,
  FileText,
  Percent,
  GripVertical,
  Loader2,
} from 'lucide-react';

interface ArticleLeftDrawerProps {
  article: Article | null;
  isOpen: boolean;
  onClose: () => void;
  onHandoffToTool: (breakdown: MacroBreakdownResponse, article: Article) => void;
}

export const ArticleLeftDrawer: React.FC<ArticleLeftDrawerProps> = ({
  article,
  isOpen,
  onClose,
  onHandoffToTool,
}) => {
  const { language, t } = useLanguage();
  const [width, setWidth] = useState<number>(() => Math.min(650, window.innerWidth * 0.5));
  const [isDragging, setIsDragging] = useState(false);
  const [activeTab, setActiveTab] = useState<'breakdown' | 'source'>('breakdown');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [breakdown, setBreakdown] = useState<MacroBreakdownResponse | null>(null);

  const drawerRef = useRef<HTMLDivElement>(null);

  // Trigger breakdown API whenever a new article is opened
  useEffect(() => {
    if (isOpen && article) {
      setLoading(true);
      setError(null);
      setBreakdown(null);

      const contentToAnalyze = article.content || article.chapeau || article.title;
      analyzeArticleBreakdown(contentToAnalyze, article.url)
        .then((data) => {
          setBreakdown(data);
        })
        .catch((err) => {
          setError(err.message || 'Lỗi khi gọi API phân tích bài viết');
        })
        .finally(() => {
          setLoading(false);
        });
    }
  }, [isOpen, article]);

  // Handle Drag to Resize on right edge
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      const newWidth = Math.max(380, Math.min(e.clientX, window.innerWidth * 0.88));
      setWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    } else {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isDragging]);

  if (!isOpen || !article) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden pointer-events-none">
      {/* Dimmed backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-2xs transition-opacity pointer-events-auto"
        onClick={onClose}
      />

      {/* Resizable Left-Docked Window */}
      <div
        ref={drawerRef}
        style={{ width: `${width}px` }}
        className="fixed top-0 bottom-0 left-0 bg-white shadow-2xl flex flex-col pointer-events-auto border-r border-gray-300 z-50 transition-[width] duration-75 ease-out"
      >
        {/* Resize Handle on Right Edge */}
        <div
          onMouseDown={handleMouseDown}
          className={`absolute top-0 bottom-0 right-0 w-3 cursor-col-resize hover:bg-brand-accent/20 active:bg-brand-accent transition-colors flex items-center justify-center group ${
            isDragging ? 'bg-brand-accent text-white' : 'text-gray-400'
          }`}
          title={t('drawer_resize_tip')}
        >
          <GripVertical className="w-3.5 h-3.5 opacity-60 group-hover:opacity-100" />
        </div>

        {/* Header Bar */}
        <div className="p-4 bg-brand-dark text-white border-b border-gray-800 flex items-center justify-between shrink-0">
          <div className="flex items-center space-x-2">
            <span className="w-2.5 h-2.5 rounded-full bg-brand-accent status-pulse-green"></span>
            <span className="font-mono text-xs uppercase tracking-wider text-red-300 font-bold">
              {t('drawer_title')}
            </span>
            <span className="text-[10px] font-mono text-gray-400 bg-gray-800 px-1.5 py-0.5 rounded">
              {Math.round((width / window.innerWidth) * 100)}% Width
            </span>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={() => setWidth(window.innerWidth * 0.75)}
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-800 rounded transition-colors"
              title="Phóng to 75%"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-800 rounded transition-colors"
              title="Đóng cửa sổ"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Article Identification Banner */}
        <div className="px-5 py-3 bg-gray-50 border-b border-gray-200 shrink-0">
          <div className="text-[10px] font-mono uppercase tracking-wider text-gray-500 mb-1 flex items-center gap-1.5">
            <FileText className="w-3 h-3 text-brand-accent" />
            <span>Nguồn: {article.source}</span>
            <span>•</span>
            <span className="text-emerald-700 font-bold">Phạm vi: Duy nhất văn bản bài báo này</span>
          </div>
          <h3 className="font-serif font-bold text-sm sm:text-base text-brand-dark line-clamp-2">
            {article.title}
          </h3>
        </div>

        {/* Tab Controls */}
        <div className="flex border-b border-gray-200 px-5 bg-white shrink-0">
          <button
            onClick={() => setActiveTab('breakdown')}
            className={`py-2.5 text-xs font-mono font-semibold uppercase tracking-wider border-b-2 mr-6 transition-all flex items-center gap-1.5 ${
              activeTab === 'breakdown'
                ? 'border-brand-accent text-brand-accent font-bold'
                : 'border-transparent text-gray-500 hover:text-gray-900'
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            {t('drawer_tab_breakdown')}
          </button>
          <button
            onClick={() => setActiveTab('source')}
            className={`py-2.5 text-xs font-mono font-semibold uppercase tracking-wider border-b-2 transition-all flex items-center gap-1.5 ${
              activeTab === 'source'
                ? 'border-brand-accent text-brand-accent font-bold'
                : 'border-transparent text-gray-500 hover:text-gray-900'
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            {t('drawer_tab_article')}
          </button>
        </div>

        {/* Main Content Area */}
        <div className="flex-1 overflow-y-auto p-5 sm:p-6 space-y-6">
          {loading && (
            <div className="py-16 text-center space-y-4">
              <Loader2 className="w-8 h-8 text-brand-accent animate-spin mx-auto" />
              <div className="text-xs font-mono text-gray-600">
                Đang gọi API backend đọc nội dung văn bản và bóc tách tác động vi mô...
              </div>
              <div className="text-[11px] font-mono text-gray-400">
                POST /api/v1/reading-room/breakdown
              </div>
            </div>
          )}

          {error && (
            <div className="p-4 bg-red-50 border border-red-200 text-red-800 text-xs rounded-sm space-y-2">
              <div className="flex items-center gap-2 font-bold font-mono">
                <AlertCircle className="w-4 h-4 text-red-600" />
                <span>Thông báo kết nối API:</span>
              </div>
              <div>{error}</div>
            </div>
          )}

          {!loading && breakdown && activeTab === 'breakdown' && (
            <div className="space-y-6 font-sans">
              
              {/* 1. Core Macro Thesis */}
              <div className="bg-brand-subtle border-l-4 border-brand-accent p-4 shadow-2xs">
                <span className="text-[10px] font-mono uppercase tracking-widest text-brand-accent font-bold block mb-1">
                  {t('drawer_macro_thesis')}
                </span>
                <p className="font-serif text-sm font-semibold text-brand-dark leading-relaxed">
                  {breakdown.original_macro_thesis}
                </p>
              </div>

              {/* 2. Middle School Layman Explanation */}
              <div className="border border-gray-200 p-4 bg-white shadow-2xs">
                <div className="flex items-center gap-2 text-xs font-mono font-bold uppercase tracking-wider text-gray-700 mb-2">
                  <span className="p-1 bg-amber-100 text-amber-900 rounded">💡</span>
                  <span>{t('drawer_layman_title')}</span>
                </div>
                <p className="text-xs sm:text-sm text-gray-700 leading-relaxed font-sans">
                  {breakdown.layman_summary}
                </p>
              </div>

              {/* 3. Micro Household Impacts */}
              <div className="space-y-3">
                <div className="flex items-center justify-between border-b border-gray-200 pb-2">
                  <h4 className="font-mono text-xs font-bold uppercase tracking-wider text-brand-dark flex items-center gap-1.5">
                    <Percent className="w-3.5 h-3.5 text-brand-accent" />
                    <span>{t('drawer_household_impacts')}</span>
                  </h4>
                  <span className="text-[10px] font-mono text-gray-500">
                    {breakdown.micro_impacts.length} nhóm chi tiêu
                  </span>
                </div>

                <div className="grid grid-cols-1 gap-2.5">
                  {breakdown.micro_impacts.map((item, idx) => (
                    <div
                      key={idx}
                      className="p-3 border border-gray-200 bg-gray-50/60 hover:bg-white hover:border-brand-accent transition-all text-xs"
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="font-bold text-brand-dark font-sans">{item.category}</span>
                        <span
                          className={`font-mono font-bold text-[11px] px-2 py-0.5 rounded ${
                            item.expected_change_pct >= 0
                              ? 'bg-red-100 text-red-800'
                              : 'bg-emerald-100 text-emerald-800'
                          }`}
                        >
                          {item.expected_change_pct >= 0 ? '+' : ''}
                          {item.expected_change_pct}%
                        </span>
                      </div>
                      <p className="text-gray-600 leading-normal text-[11px]">
                        {item.layman_explanation}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              {/* 4. Recommended Actions */}
              <div className="bg-emerald-50/50 border border-emerald-200 p-4 space-y-2">
                <h4 className="font-mono text-xs font-bold uppercase tracking-wider text-emerald-900 flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-700" />
                  <span>{t('drawer_recommended_actions')}</span>
                </h4>
                <ul className="space-y-2 text-xs text-gray-700 font-sans">
                  {breakdown.recommended_actions.map((act, idx) => (
                    <li key={idx} className="flex items-start gap-2">
                      <span className="font-mono text-emerald-700 font-bold">[{idx + 1}]</span>
                      <span>{act}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* 5. Confidence Score */}
              <div className="flex items-center justify-between text-xs font-mono text-gray-500 pt-2 border-t border-gray-100">
                <span>{t('drawer_confidence')}:</span>
                <span className="font-bold text-brand-dark">
                  {Math.round((breakdown.confidence_score || 0.95) * 100)}% (Calibrated NLP)
                </span>
              </div>

            </div>
          )}

          {activeTab === 'source' && (
            <div className="space-y-4 font-serif text-sm text-gray-800 leading-relaxed whitespace-pre-line">
              <div className="text-xs font-mono text-gray-500 bg-gray-50 p-3 border border-gray-200 font-sans">
                Độ dài văn bản: {article.content.length} ký tự. Văn bản được dùng độc lập làm đầu vào cho LLM Breakdown.
              </div>
              <div className="p-4 bg-white border border-gray-200">
                {article.content}
              </div>
            </div>
          )}
        </div>

        {/* Footer Action (Data Handoff to Section III) */}
        <div className="p-4 sm:p-5 bg-gray-50 border-t border-gray-200 shrink-0">
          <button
            type="button"
            disabled={!breakdown}
            onClick={() => {
              if (breakdown) {
                onHandoffToTool(breakdown, article);
              }
            }}
            className="w-full py-3 px-4 bg-brand-accent hover:bg-brand-accent-hover disabled:bg-gray-400 text-white font-mono text-xs font-bold uppercase tracking-wider transition-all flex items-center justify-center gap-2 shadow-sm"
          >
            <span>{t('drawer_btn_handoff')}</span>
            <ArrowRight className="w-4 h-4" />
          </button>
          <div className="text-[10px] font-mono text-center text-gray-500 mt-2">
            Chuyển dữ liệu vĩ mô sang VS Code Studio (Mục III) làm biến số đầu vào
          </div>
        </div>

      </div>
    </div>
  );
};
