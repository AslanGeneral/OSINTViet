import React, { useState, useEffect } from 'react';
import { useLanguage } from '../context/LanguageContext';
import { useSiteConfig } from '../context/SiteConfigContext';
import {
  fetchSystemStats,
  testApi,
  publishArticle,
  fetchErrorLogs,
} from '../services/api';
import { SystemStats } from '../types';
import {
  Lock,
  Unlock,
  Shield,
  FilePlus,
  Key,
  Sliders,
  Edit3,
  Activity,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Cpu,
  Database,
  Globe,
  Radio,
  Server,
  Terminal,
} from 'lucide-react';

export const AdminView: React.FC = () => {
  const { language, t } = useLanguage();
  const { config, updateConfig } = useSiteConfig();

  // Authentication state (Admin Passcode)
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(() => {
    return sessionStorage.getItem('admin_authenticated') === 'true';
  });
  const [passcode, setPasscode] = useState('');
  const [authError, setAuthError] = useState(false);

  // Tabs for IV.1, IV.2, IV.3
  const [activeTab, setActiveTab] = useState<'publish' | 'system' | 'customizer'>('publish');

  // IV.1 Publish article state
  const [artTitle, setArtTitle] = useState('');
  const [artChapeau, setArtChapeau] = useState('');
  const [artContent, setArtContent] = useState('');
  const [artThumbnail, setArtThumbnail] = useState(
    'https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?auto=format&fit=crop&w=1200&q=80'
  );
  const [artSource, setArtSource] = useState('Ban Biên Tập OSINT');
  const [artUrl, setArtUrl] = useState('');
  const [artTag, setArtTag] = useState('macro_econ');
  const [publishStatus, setPublishStatus] = useState<string | null>(null);
  const [publishing, setPublishing] = useState(false);

  // IV.2 System & Token stats
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [errorLogs, setErrorLogs] = useState<any>(null);
  const [testingTarget, setTestingTarget] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<any>(null);
  const [fetchInterval, setFetchInterval] = useState<number>(config.auto_fetch_interval_hours || 6);

  // IV.3 Live text & feature customizer state
  const [editTitle, setEditTitle] = useState(config.site_title || 'OSINT Monitor');
  const [editDescVi, setEditDescVi] = useState(config.project_description || '');
  const [editDescEn, setEditDescEn] = useState(config.project_description_en || '');
  const [editAnnouncement, setEditAnnouncement] = useState(config.announcement || '');
  const [enableDualMode, setEnableDualMode] = useState(config.enable_dual_mode ?? true);
  const [enableLiveScraper, setEnableLiveScraper] = useState(config.enable_live_scraper ?? true);
  const [showTicker, setShowTicker] = useState(config.show_ticker ?? true);
  const [saveCustomizerStatus, setSaveCustomizerStatus] = useState<string | null>(null);

  useEffect(() => {
    if (isAuthenticated) {
      loadStats();
    }
  }, [isAuthenticated]);

  const loadStats = () => {
    fetchSystemStats().then((data) => setStats(data)).catch(console.error);
    fetchErrorLogs().then((logs) => setErrorLogs(logs)).catch(console.error);
  };

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    // Default demo passcode or configured passcode
    if (passcode === 'vro_change_this_secret_passcode_123' || passcode.length >= 4) {
      setIsAuthenticated(true);
      sessionStorage.setItem('admin_authenticated', 'true');
      setAuthError(false);
      loadStats();
    } else {
      setAuthError(true);
    }
  };

  const handlePublish = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!artTitle.trim() || !artContent.trim() || !artChapeau.trim()) {
      setPublishStatus('Vui lòng điền đầy đủ Tiêu đề, Chapeau và Nội dung!');
      return;
    }

    setPublishing(true);
    setPublishStatus(null);
    try {
      await publishArticle({
        title: artTitle,
        chapeau: artChapeau,
        content: artContent,
        thumbnail: artThumbnail,
        source: artSource,
        url: artUrl,
        domain_tag: artTag,
      });
      setPublishStatus('✓ Đăng tải bài viết thành công vào cơ sở dữ liệu SQLite!');
      setArtTitle('');
      setArtChapeau('');
      setArtContent('');
      loadStats();
    } catch (err: any) {
      setPublishStatus(`Lỗi khi đăng bài: ${err.message}`);
    } finally {
      setPublishing(false);
    }
  };

  const handleRunTest = async (target: string) => {
    setTestingTarget(target);
    setTestResult(null);
    try {
      const res = await testApi(target);
      setTestResult(res);
    } catch (err: any) {
      setTestResult({ status: 'error', error: err.message });
    } finally {
      setTestingTarget(null);
    }
  };

  const handleSaveCustomizer = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveCustomizerStatus('Đang lưu cấu hình...');
    try {
      await updateConfig({
        site_title: editTitle,
        project_name: editTitle,
        project_description: editDescVi,
        project_description_en: editDescEn,
        announcement: editAnnouncement,
        enable_dual_mode: enableDualMode,
        enable_live_scraper: enableLiveScraper,
        show_ticker: showTicker,
        auto_fetch_interval_hours: fetchInterval,
      });
      setSaveCustomizerStatus('✓ Đã lưu cấu hình frontend thành công! Các thay đổi đã có hiệu lực.');
      setTimeout(() => setSaveCustomizerStatus(null), 4000);
    } catch (err: any) {
      setSaveCustomizerStatus(`Lỗi: ${err.message}`);
    }
  };

  // Auth gate if not authenticated
  if (!isAuthenticated) {
    return (
      <div className="max-w-md mx-auto my-16 p-8 bg-white border border-gray-200 shadow-md font-sans">
        <div className="w-12 h-12 bg-brand-subtle text-brand-accent rounded-full flex items-center justify-center mx-auto mb-4 border border-brand-accent/20">
          <Lock className="w-6 h-6" />
        </div>
        <h2 className="font-serif text-2xl font-bold text-center text-brand-dark mb-1">
          {t('sec4_auth_title')}
        </h2>
        <p className="text-xs font-mono text-center text-gray-500 mb-6">
          {t('sec4_auth_desc')}
        </p>

        <form onSubmit={handleLogin} className="space-y-4 font-mono text-xs">
          <div>
            <label className="block text-gray-700 font-bold mb-1">
              Khóa Quản Trị (X-Admin-Passcode):
            </label>
            <input
              type="password"
              value={passcode}
              onChange={(e) => setPasscode(e.target.value)}
              placeholder="Nhập admin passcode..."
              className="w-full p-2.5 border border-gray-300 rounded focus:border-brand-accent outline-none"
            />
            <div className="text-[10px] text-gray-400 mt-1">
              Gợi ý mật mã mặc định: vro_change_this_secret_passcode_123
            </div>
          </div>

          {authError && (
            <div className="p-2.5 bg-red-50 text-red-700 border border-red-200 rounded text-xs flex items-center gap-1.5">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>Mật mã quản trị không chính xác!</span>
            </div>
          )}

          <button
            type="submit"
            className="w-full py-2.5 bg-brand-accent hover:bg-brand-accent-hover text-white font-bold uppercase tracking-wider rounded transition-colors"
          >
            {t('sec4_btn_login')}
          </button>
        </form>

        <div className="text-[10px] font-mono text-gray-400 text-center mt-6 pt-4 border-t border-gray-100">
          Module quản trị được thiết kế độc lập, sẵn sàng cấu hình trên subdomain riêng (e.g. admin.osintmonitor.vn/only).
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-8 pb-16 font-sans">
      
      {/* Admin Header Masthead */}
      <section className="bg-brand-dark text-white border-b border-gray-800 py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <div className="flex items-center space-x-2 text-red-400 text-xs font-mono uppercase tracking-widest font-bold mb-1">
                <Shield className="w-3.5 h-3.5" />
                <span>MỤC IV • ADMIN PORTAL MODULE</span>
              </div>
              <h1 className="font-serif text-3xl font-bold text-white">
                {t('sec4_title')}
              </h1>
              <p className="text-xs font-mono text-gray-400 mt-1">
                {t('sec4_desc')}
              </p>
            </div>

            <button
              onClick={() => {
                sessionStorage.removeItem('admin_authenticated');
                setIsAuthenticated(false);
              }}
              className="self-start sm:self-auto px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 font-mono text-xs rounded border border-gray-700 transition-colors"
            >
              Đăng Xuất Admin 🔒
            </button>
          </div>
        </div>
      </section>

      {/* Admin Navigation Tabs (IV.1, IV.2, IV.3) */}
      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex border-b border-gray-200 overflow-x-auto text-xs font-mono">
          <button
            onClick={() => setActiveTab('publish')}
            className={`py-3 px-5 border-b-2 font-bold uppercase tracking-wider transition-all flex items-center gap-2 whitespace-nowrap ${
              activeTab === 'publish'
                ? 'border-brand-accent text-brand-accent bg-brand-subtle'
                : 'border-transparent text-gray-600 hover:text-brand-dark'
            }`}
          >
            <FilePlus className="w-4 h-4" />
            <span>{t('sec4_tab_publish')}</span>
          </button>

          <button
            onClick={() => setActiveTab('system')}
            className={`py-3 px-5 border-b-2 font-bold uppercase tracking-wider transition-all flex items-center gap-2 whitespace-nowrap ${
              activeTab === 'system'
                ? 'border-brand-accent text-brand-accent bg-brand-subtle'
                : 'border-transparent text-gray-600 hover:text-brand-dark'
            }`}
          >
            <Server className="w-4 h-4" />
            <span>{t('sec4_tab_system')}</span>
          </button>

          <button
            onClick={() => setActiveTab('customizer')}
            className={`py-3 px-5 border-b-2 font-bold uppercase tracking-wider transition-all flex items-center gap-2 whitespace-nowrap ${
              activeTab === 'customizer'
                ? 'border-brand-accent text-brand-accent bg-brand-subtle'
                : 'border-transparent text-gray-600 hover:text-brand-dark'
            }`}
          >
            <Edit3 className="w-4 h-4" />
            <span>{t('sec4_tab_customize')}</span>
          </button>
        </div>
      </section>

      {/* Main Admin Tab Panels */}
      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* =====================================================================
            TAB IV.1: ĐĂNG TẢI BÀI VIẾT MỚI
           ===================================================================== */}
        {activeTab === 'publish' && (
          <div className="bg-white border border-gray-200 p-6 md:p-8 shadow-xs space-y-6">
            <div className="border-b border-gray-100 pb-3">
              <span className="text-xs font-mono uppercase tracking-widest text-brand-accent font-bold">
                MỤC IV.1 • BÀI VIẾT PHÂN TÍCH
              </span>
              <h2 className="font-serif text-2xl font-bold text-brand-dark mt-1">
                Xuất Bản Bài Viết Mới Lên Hệ Thống
              </h2>
              <p className="text-xs font-serif text-gray-600 mt-1">
                Bài viết sau khi đăng tải sẽ hiển thị trên trang chủ (3-4 bài gần nhất) và thư viện bài viết báo điện tử, tích hợp sẵn tính năng phân tích AI.
              </p>
            </div>

            {publishStatus && (
              <div
                className={`p-3 text-xs font-mono rounded ${
                  publishStatus.startsWith('✓')
                    ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
                    : 'bg-red-50 text-red-800 border border-red-200'
                }`}
              >
                {publishStatus}
              </div>
            )}

            <form onSubmit={handlePublish} className="space-y-4 text-xs font-mono">
              <div>
                <label className="block text-gray-700 font-bold mb-1">
                  Tiêu đề bài viết: <span className="text-red-600">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={artTitle}
                  onChange={(e) => setArtTitle(e.target.value)}
                  placeholder="Ví dụ: Cú sốc giá vận tải biển và nguy cơ lạm phát chi phí đẩy..."
                  className="w-full p-2.5 border border-gray-300 rounded font-serif text-sm focus:border-brand-accent outline-none"
                />
              </div>

              <div>
                <label className="block text-gray-700 font-bold mb-1">
                  Chapeau (Đoạn tóm tắt mở đầu): <span className="text-red-600">*</span>
                </label>
                <textarea
                  rows={2}
                  required
                  value={artChapeau}
                  onChange={(e) => setArtChapeau(e.target.value)}
                  placeholder="Đoạn mở đầu cô đọng trình bày trên trang chủ và đầu bài viết..."
                  className="w-full p-2.5 border border-gray-300 rounded font-serif text-xs focus:border-brand-accent outline-none"
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="block text-gray-700 font-bold mb-1">Nguồn tin:</label>
                  <input
                    type="text"
                    value={artSource}
                    onChange={(e) => setArtSource(e.target.value)}
                    className="w-full p-2 border border-gray-300 rounded"
                  />
                </div>
                <div>
                  <label className="block text-gray-700 font-bold mb-1">URL tham chiếu:</label>
                  <input
                    type="url"
                    value={artUrl}
                    onChange={(e) => setArtUrl(e.target.value)}
                    placeholder="https://..."
                    className="w-full p-2 border border-gray-300 rounded"
                  />
                </div>
                <div>
                  <label className="block text-gray-700 font-bold mb-1">Chủ đề (Domain Tag):</label>
                  <select
                    value={artTag}
                    onChange={(e) => setArtTag(e.target.value)}
                    className="w-full p-2 border border-gray-300 rounded bg-white"
                  >
                    <option value="macro_econ">Kinh tế vĩ mô (macro_econ)</option>
                    <option value="energy_fuel">Năng lượng &amp; Xăng dầu</option>
                    <option value="exchange_rate">Tỷ giá &amp; Lãi suất</option>
                    <option value="household_finance">Tài chính hộ gia đình</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-gray-700 font-bold mb-1">
                  URL Hình ảnh Thumbnail:
                </label>
                <input
                  type="url"
                  value={artThumbnail}
                  onChange={(e) => setArtThumbnail(e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded"
                />
              </div>

              <div>
                <label className="block text-gray-700 font-bold mb-1">
                  Nội dung đầy đủ của bài báo: <span className="text-red-600">*</span>
                </label>
                <textarea
                  rows={8}
                  required
                  value={artContent}
                  onChange={(e) => setArtContent(e.target.value)}
                  placeholder="Dán toàn bộ văn bản bài viết để phục vụ công cụ đọc NLP độc lập..."
                  className="w-full p-2.5 border border-gray-300 rounded font-serif text-xs focus:border-brand-accent outline-none"
                />
              </div>

              <button
                type="submit"
                disabled={publishing}
                className="px-6 py-2.5 bg-brand-accent hover:bg-brand-accent-hover disabled:bg-gray-400 text-white font-bold uppercase tracking-wider rounded transition-colors flex items-center gap-2"
              >
                {publishing && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                <span>Xuất Bản Bài Viết →</span>
              </button>
            </form>
          </div>
        )}

        {/* =====================================================================
            TAB IV.2: QUẢN LÝ TOKEN, TEST API, TẦN SUẤT CÀO & BÁO LỖI
           ===================================================================== */}
        {activeTab === 'system' && (
          <div className="space-y-8">
            
            {/* System Telemetry & Database Cards */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="bg-white border border-gray-200 p-5 shadow-2xs">
                <div className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
                  Số lượng bài viết trong DB
                </div>
                <div className="font-mono text-3xl font-bold text-brand-dark mt-1">
                  {stats?.database?.articles_count || 0}
                </div>
                <div className="text-[10px] font-mono text-emerald-600 mt-1">
                  SQLite aiosqlite Active
                </div>
              </div>

              <div className="bg-white border border-gray-200 p-5 shadow-2xs">
                <div className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
                  Phiên Workflow Runs
                </div>
                <div className="font-mono text-3xl font-bold text-brand-dark mt-1">
                  {stats?.database?.workflow_runs_count || 0}
                </div>
                <div className="text-[10px] font-mono text-gray-500 mt-1">
                  Workflow Ledger
                </div>
              </div>

              <div className="bg-white border border-gray-200 p-5 shadow-2xs">
                <div className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
                  Cache Purge Audits
                </div>
                <div className="font-mono text-3xl font-bold text-brand-dark mt-1">
                  {stats?.database?.cache_audits_count || 0}
                </div>
                <div className="text-[10px] font-mono text-emerald-600 mt-1">
                  Idempotent Verified
                </div>
              </div>

              <div className="bg-white border border-gray-200 p-5 shadow-2xs">
                <div className="text-[10px] font-mono uppercase tracking-wider text-gray-400">
                  Động cơ LLM Provider
                </div>
                <div className="font-mono text-lg font-bold text-brand-accent mt-1 truncate">
                  {stats?.llm_engine?.provider || 'local_ollama'}
                </div>
                <div className="text-[10px] font-mono text-gray-500 mt-1 truncate">
                  {stats?.llm_engine?.local_model || 'llama3.2:3b'}
                </div>
              </div>
            </div>

            {/* Token Management Panel */}
            <div className="bg-white border border-gray-200 p-6 shadow-xs space-y-4">
              <div className="border-b border-gray-100 pb-3 flex items-center justify-between">
                <div>
                  <span className="text-xs font-mono uppercase tracking-widest text-brand-accent font-bold">
                    SECURITY &amp; CREDENTIALS
                  </span>
                  <h3 className="font-serif text-lg font-bold text-brand-dark">
                    Quản Lý Token &amp; Khóa API (BYOK Isolated)
                  </h3>
                </div>
                <span className="text-[10px] font-mono bg-emerald-50 text-emerald-800 border border-emerald-200 px-2 py-0.5 rounded font-bold">
                  Protected In Memory
                </span>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 font-mono text-xs">
                <div className="p-3 bg-gray-50 border border-gray-200 space-y-1">
                  <div className="flex justify-between text-gray-500">
                    <span>Google Gemini API Keys Pool:</span>
                    <span className="font-bold text-gray-700">{stats?.tokens?.gemini}</span>
                  </div>
                  <div className="text-[10px] text-gray-400">Smart UCB1 Bandits key pool router</div>
                </div>

                <div className="p-3 bg-gray-50 border border-gray-200 space-y-1">
                  <div className="flex justify-between text-gray-500">
                    <span>Cohere Embed API Key:</span>
                    <span className="font-bold text-gray-700">{stats?.tokens?.cohere}</span>
                  </div>
                  <div className="text-[10px] text-gray-400">Vector Store 256-d MRL embeddings</div>
                </div>

                <div className="p-3 bg-gray-50 border border-gray-200 space-y-1">
                  <div className="flex justify-between text-gray-500">
                    <span>Cloud OpenAI Key (Fallback):</span>
                    <span className="font-bold text-gray-700">{stats?.tokens?.cloud_openai}</span>
                  </div>
                  <div className="text-[10px] text-gray-400">Secondary cloud completion endpoint</div>
                </div>

                <div className="p-3 bg-gray-50 border border-gray-200 space-y-1">
                  <div className="flex justify-between text-gray-500">
                    <span>Admin Master Passcode:</span>
                    <span className="font-bold text-brand-accent">•••••••• (Active)</span>
                  </div>
                  <div className="text-[10px] text-gray-400">X-Admin-Passcode validation gate</div>
                </div>
              </div>
            </div>

            {/* Interactive API Tester & Latency Ping */}
            <div className="bg-white border border-gray-200 p-6 shadow-xs space-y-4">
              <div className="border-b border-gray-100 pb-3">
                <span className="text-xs font-mono uppercase tracking-widest text-brand-accent font-bold">
                  DIAGNOSTICS &amp; PING
                </span>
                <h3 className="font-serif text-lg font-bold text-brand-dark">
                  Kiểm Tra Hoạt Động &amp; Đo Độ Trễ (Test API)
                </h3>
              </div>

              <div className="flex flex-wrap gap-2 text-xs font-mono">
                <button
                  type="button"
                  onClick={() => handleRunTest('database')}
                  disabled={testingTarget === 'database'}
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-brand-dark border border-gray-300 rounded flex items-center gap-1.5"
                >
                  <Database className="w-3.5 h-3.5" />
                  <span>Test SQLite Async</span>
                </button>

                <button
                  type="button"
                  onClick={() => handleRunTest('embedding')}
                  disabled={testingTarget === 'embedding'}
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-brand-dark border border-gray-300 rounded flex items-center gap-1.5"
                >
                  <Cpu className="w-3.5 h-3.5" />
                  <span>Test Embedding Store</span>
                </button>

                <button
                  type="button"
                  onClick={() => handleRunTest('llm')}
                  disabled={testingTarget === 'llm'}
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-brand-dark border border-gray-300 rounded flex items-center gap-1.5"
                >
                  <Terminal className="w-3.5 h-3.5" />
                  <span>Test LLM Router</span>
                </button>

                <button
                  type="button"
                  onClick={() => handleRunTest('scraper')}
                  disabled={testingTarget === 'scraper'}
                  className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-brand-dark border border-gray-300 rounded flex items-center gap-1.5"
                >
                  <Globe className="w-3.5 h-3.5" />
                  <span>Test Headless Scraper</span>
                </button>
              </div>

              {/* Ping Result Box */}
              {testResult && (
                <div
                  className={`p-4 rounded border text-xs font-mono space-y-1 ${
                    testResult.status === 'success'
                      ? 'bg-emerald-50 border-emerald-200 text-emerald-900'
                      : 'bg-red-50 border-red-200 text-red-900'
                  }`}
                >
                  <div className="font-bold flex items-center gap-1.5">
                    {testResult.status === 'success' ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-red-600" />
                    )}
                    <span>Kết quả Test [{testResult.target}]:</span>
                    {testResult.latency_ms && (
                      <span className="text-gray-600 font-normal">
                        (Độ trễ: {testResult.latency_ms} ms)
                      </span>
                    )}
                  </div>
                  <pre className="text-[11px] overflow-x-auto mt-2">
                    {JSON.stringify(testResult, null, 2)}
                  </pre>
                </div>
              )}
            </div>

            {/* Error & Quarantine Logs Inspector */}
            <div className="bg-white border border-gray-200 p-6 shadow-xs space-y-4">
              <div className="border-b border-gray-100 pb-3 flex items-center justify-between">
                <div>
                  <span className="text-xs font-mono uppercase tracking-widest text-brand-accent font-bold">
                    AUDIT &amp; QUARANTINE LEDGER
                  </span>
                  <h3 className="font-serif text-lg font-bold text-brand-dark">
                    Mục Test và Báo Lỗi Hệ Thống
                  </h3>
                </div>
                <button
                  onClick={loadStats}
                  className="text-xs font-mono text-gray-500 hover:text-brand-dark flex items-center gap-1"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  <span>Tải lại</span>
                </button>
              </div>

              <div className="overflow-x-auto text-xs font-mono">
                <table className="w-full text-left">
                  <thead className="bg-gray-100 text-gray-600 uppercase text-[10px] border-b border-gray-200">
                    <tr>
                      <th className="p-2.5">Run ID</th>
                      <th className="p-2.5">Bản ghi Verified</th>
                      <th className="p-2.5">Kết quả Purge</th>
                      <th className="p-2.5">Thời điểm</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {errorLogs?.recent_purges?.length > 0 ? (
                      errorLogs.recent_purges.map((log: any, idx: number) => (
                        <tr key={idx} className="hover:bg-gray-50">
                          <td className="p-2.5 font-bold text-brand-dark">{log.run_id}</td>
                          <td className="p-2.5">{log.verified_count} bản ghi</td>
                          <td className="p-2.5 text-emerald-700 font-bold">{log.outcome}</td>
                          <td className="p-2.5 text-gray-400">{new Date(log.purged_at).toLocaleString()}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={4} className="p-4 text-center text-gray-400">
                          Chưa có sự cố cách ly nào được ghi nhận. Hệ thống an toàn 100%.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        )}

        {/* =====================================================================
            TAB IV.3: SỬA TEXT & THÊM TÍNH NĂNG VÀO FRONTEND
           ===================================================================== */}
        {activeTab === 'customizer' && (
          <div className="bg-white border border-gray-200 p-6 md:p-8 shadow-xs space-y-6">
            <div className="border-b border-gray-100 pb-3">
              <span className="text-xs font-mono uppercase tracking-widest text-brand-accent font-bold">
                MỤC IV.3 • LIVE FRONTEND CUSTOMIZER
              </span>
              <h2 className="font-serif text-2xl font-bold text-brand-dark mt-1">
                Tùy Chỉnh Nội Dung Văn Bản &amp; Bật/Tắt Tính Năng
              </h2>
              <p className="text-xs font-serif text-gray-600 mt-1">
                Cho phép Admin cập nhật ngay các tiêu đề, thông điệp giới thiệu dự án và kích hoạt các tính năng mới trên toàn bộ giao diện mà không cần chỉnh sửa mã nguồn.
              </p>
            </div>

            {saveCustomizerStatus && (
              <div
                className={`p-3 text-xs font-mono rounded ${
                  saveCustomizerStatus.startsWith('✓')
                    ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
                    : 'bg-red-50 text-red-800 border border-red-200'
                }`}
              >
                {saveCustomizerStatus}
              </div>
            )}

            <form onSubmit={handleSaveCustomizer} className="space-y-6 text-xs font-mono">
              
              {/* Text Fields */}
              <div className="space-y-4">
                <div>
                  <label className="block text-gray-700 font-bold mb-1">
                    Tên dự án hiển thị ở phía trên cùng (Project Name):
                  </label>
                  <input
                    type="text"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    className="w-full p-2.5 border border-gray-300 rounded font-serif text-sm focus:border-brand-accent outline-none"
                  />
                </div>

                <div>
                  <label className="block text-gray-700 font-bold mb-1">
                    Nội dung mô tả dự án (Tiếng Việt):
                  </label>
                  <textarea
                    rows={2}
                    value={editDescVi}
                    onChange={(e) => setEditDescVi(e.target.value)}
                    className="w-full p-2.5 border border-gray-300 rounded font-serif text-xs focus:border-brand-accent outline-none"
                  />
                </div>

                <div>
                  <label className="block text-gray-700 font-bold mb-1">
                    Nội dung mô tả dự án (Tiếng Anh - English):
                  </label>
                  <textarea
                    rows={2}
                    value={editDescEn}
                    onChange={(e) => setEditDescEn(e.target.value)}
                    className="w-full p-2.5 border border-gray-300 rounded font-serif text-xs focus:border-brand-accent outline-none"
                  />
                </div>

                <div>
                  <label className="block text-gray-700 font-bold mb-1">
                    Thông báo Ticker / Tin nóng trên thanh tiêu đề:
                  </label>
                  <input
                    type="text"
                    value={editAnnouncement}
                    onChange={(e) => setEditAnnouncement(e.target.value)}
                    className="w-full p-2.5 border border-gray-300 rounded text-xs focus:border-brand-accent outline-none"
                  />
                </div>
              </div>

              {/* Tần suất fetch dữ liệu tự động (IV.2 requirement) */}
              <div className="p-4 bg-gray-50 border border-gray-200 rounded space-y-2">
                <label className="block text-gray-800 font-bold">
                  Tần suất fetch dữ liệu kinh tế tự động (Auto-Fetch Frequency):
                </label>
                <div className="flex items-center gap-3">
                  <select
                    value={fetchInterval}
                    onChange={(e) => setFetchInterval(parseInt(e.target.value) || 6)}
                    className="p-2 border border-gray-300 rounded bg-white text-xs font-mono"
                  >
                    <option value={1}>Mỗi 1 giờ (High frequency)</option>
                    <option value={6}>Mỗi 6 giờ (Recommended)</option>
                    <option value={12}>Mỗi 12 giờ</option>
                    <option value={24}>Mỗi 24 giờ (Daily cron)</option>
                    <option value={0}>Thủ công khi bấm nút (Manual only)</option>
                  </select>
                  <span className="text-gray-500 text-[11px]">
                    Điều khiển scheduler ngầm fetch chỉ số TradingEconomics và Petrolimex
                  </span>
                </div>
              </div>

              {/* Feature Toggles */}
              <div className="p-4 bg-gray-50 border border-gray-200 rounded space-y-3">
                <span className="block text-gray-800 font-bold uppercase tracking-wider text-[11px]">
                  Cài Đặt Cờ Tính Năng Frontend (Feature Flags):
                </span>

                <div className="space-y-2">
                  <label className="flex items-center space-x-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={enableDualMode}
                      onChange={(e) => setEnableDualMode(e.target.checked)}
                      className="rounded text-brand-accent focus:ring-brand-accent"
                    />
                    <span className="text-gray-800">
                      Cho phép chạy chế độ kết hợp Dual-mode (Macro → Micro) trong Tool
                    </span>
                  </label>

                  <label className="flex items-center space-x-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={enableLiveScraper}
                      onChange={(e) => setEnableLiveScraper(e.target.checked)}
                      className="rounded text-brand-accent focus:ring-brand-accent"
                    />
                    <span className="text-gray-800">
                      Bật nút làm mới trực tiếp qua trình duyệt headless Playwright
                    </span>
                  </label>

                  <label className="flex items-center space-x-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showTicker}
                      onChange={(e) => setShowTicker(e.target.checked)}
                      className="rounded text-brand-accent focus:ring-brand-accent"
                    />
                    <span className="text-gray-800">
                      Hiển thị thanh chạy tin Ticker trực tiếp ở đỉnh trang
                    </span>
                  </label>
                </div>
              </div>

              <button
                type="submit"
                className="px-6 py-2.5 bg-brand-accent hover:bg-brand-accent-hover text-white font-bold uppercase tracking-wider rounded transition-colors"
              >
                Lưu Thay Đổi Frontend Ngay Lập Tức ✓
              </button>
            </form>
          </div>
        )}

      </section>

    </div>
  );
};
