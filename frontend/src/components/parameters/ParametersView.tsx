import React, { useState, useEffect } from 'react';
import { ParametersData, StockIndex, EconomicIndicator } from '../../types';
import { useLanguage } from '../../context/LanguageContext';
import { fetchParameters, triggerLiveScrape } from '../../services/api';
import {
  Globe,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  ExternalLink,
  ShieldCheck,
  Activity,
  Layers,
  ArrowUpRight,
  ArrowDownRight,
  Clock,
} from 'lucide-react';

export const ParametersView: React.FC = () => {
  const { language, t } = useLanguage();
  const [selectedCountry, setSelectedCountry] = useState<string>('vietnam');
  const [data, setData] = useState<ParametersData | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const countries = [
    { key: 'vietnam', label: 'Việt Nam 🇻🇳', flag: '🇻🇳', nameVi: 'Việt Nam', nameEn: 'Vietnam' },
    { key: 'us', label: 'Mỹ (United States) 🇺🇸', flag: '🇺🇸', nameVi: 'Mỹ', nameEn: 'United States' },
    { key: 'uk', label: 'Anh (United Kingdom) 🇬🇧', flag: '🇬🇧', nameVi: 'Anh', nameEn: 'United Kingdom' },
    { key: 'china', label: 'Trung Quốc (China) 🇨🇳', flag: '🇨🇳', nameVi: 'TQ', nameEn: 'China' },
  ];

  useEffect(() => {
    setLoading(true);
    fetchParameters(selectedCountry)
      .then((res) => setData(res))
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  }, [selectedCountry]);

  const handleLiveScrape = async () => {
    setRefreshing(true);
    setNotice(
      language === 'vi'
        ? 'Đang kết nối bot Chromium Playwright không đầu (headless) cào dữ liệu từ TradingEconomics...'
        : 'Connecting headless Chromium Playwright browser to fetch TradingEconomics indicators...'
    );
    try {
      await triggerLiveScrape(selectedCountry);
      // Re-fetch parameters after triggering
      setTimeout(async () => {
        const fresh = await fetchParameters(selectedCountry);
        setData(fresh);
        setRefreshing(false);
        setNotice(
          language === 'vi'
            ? 'Đã đồng bộ dữ liệu mới nhất từ TradingEconomics thành công!'
            : 'Successfully synchronized fresh metrics from TradingEconomics!'
        );
        setTimeout(() => setNotice(null), 5000);
      }, 1500);
    } catch (err) {
      setRefreshing(false);
      setNotice('Lỗi kích hoạt headless browser');
    }
  };

  return (
    <div className="space-y-8 font-sans">
      {/* Parameters Header Banner */}
      <div className="bg-white border border-gray-200 p-6 md:p-8 shadow-xs">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-gray-100 pb-5">
          <div>
            <div className="flex items-center space-x-2 text-brand-accent text-xs font-mono uppercase tracking-widest font-bold mb-1">
              <Layers className="w-3.5 h-3.5" />
              <span>MỤC III.1 • PARAMETERS &amp; MARKET METRICS</span>
            </div>
            <h2 className="font-serif text-2xl sm:text-3xl font-bold text-brand-dark">
              {t('sec3_params_title')}
            </h2>
            <p className="text-xs sm:text-sm text-gray-600 mt-1 max-w-2xl font-serif">
              {t('sec3_params_desc')}
            </p>
          </div>

          {/* Live Scrape CTA */}
          <div className="shrink-0 flex flex-col items-end">
            <button
              onClick={handleLiveScrape}
              disabled={refreshing}
              className="inline-flex items-center px-4 py-2.5 bg-brand-dark hover:bg-gray-800 disabled:bg-gray-400 text-white text-xs font-mono font-bold tracking-wider uppercase transition-colors shadow-2xs gap-2"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              <span>{t('sec3_btn_refresh_scrape')}</span>
            </button>
            <div className="text-[10px] font-mono text-gray-400 mt-1">
              Playwright Headless • Orchestrated with backend
            </div>
          </div>
        </div>

        {/* Notice alert */}
        {notice && (
          <div className="mt-4 p-3 bg-emerald-50 border border-emerald-200 text-emerald-900 text-xs font-mono flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-700 shrink-0" />
            <span>{notice}</span>
          </div>
        )}

        {/* Country Selector (Mỹ, Anh, TQ, Việt Nam) */}
        <div className="pt-6">
          <label className="block text-xs font-mono uppercase tracking-wider text-gray-500 font-bold mb-3">
            {t('sec3_country_selector_label')}
          </label>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {countries.map((c) => {
              const active = selectedCountry === c.key;
              return (
                <button
                  key={c.key}
                  type="button"
                  onClick={() => setSelectedCountry(c.key)}
                  className={`p-3 text-left border rounded-xs font-mono text-xs transition-all flex items-center justify-between ${
                    active
                      ? 'border-brand-accent bg-brand-subtle text-brand-dark font-bold shadow-2xs ring-1 ring-brand-accent'
                      : 'border-gray-200 bg-white hover:border-gray-400 text-gray-700'
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span className="text-base">{c.flag}</span>
                    <span className="font-serif font-bold text-sm">
                      {language === 'vi' ? c.nameVi : c.nameEn}
                    </span>
                  </span>
                  {active && <span className="w-2 h-2 rounded-full bg-brand-accent"></span>}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Stock Markets & Indices Cards */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-serif text-xl font-bold text-brand-dark flex items-center gap-2">
            <Activity className="w-5 h-5 text-brand-accent" />
            <span>{t('sec3_stocks_table_title')} ({data?.country_name || selectedCountry.toUpperCase()})</span>
          </h3>
          <span className="text-xs font-mono text-gray-400">
            {language === 'vi' ? 'Tiền tệ giao dịch:' : 'Currency:'} {data?.currency || 'LOCAL'}
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {data?.stocks.map((stock, idx) => {
            const isUp = stock.pct_change >= 0;
            return (
              <div
                key={idx}
                className="bg-white border border-gray-200 p-5 hover:shadow-editorial transition-all flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-center justify-between text-xs font-mono text-gray-500 mb-1">
                    <span className="font-bold text-brand-dark">{stock.symbol}</span>
                    <span
                      className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        isUp ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'
                      }`}
                    >
                      {isUp ? <ArrowUpRight className="w-3 h-3 mr-0.5" /> : <ArrowDownRight className="w-3 h-3 mr-0.5" />}
                      {stock.pct_change > 0 ? '+' : ''}
                      {stock.pct_change}%
                    </span>
                  </div>
                  <div className="text-[11px] text-gray-500 truncate mb-2">{stock.name}</div>
                  <div className="font-mono text-2xl font-bold text-brand-dark">
                    {stock.value.toLocaleString()}
                  </div>
                </div>

                <div className="pt-3 border-t border-gray-100 flex items-center justify-between text-[10px] font-mono text-gray-400 mt-4">
                  <span>Chênh lệch: {stock.change > 0 ? '+' : ''}{stock.change}</span>
                  <span>Biên: {stock.low} - {stock.high}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* TradingEconomics Macro Indicators Table */}
      <div className="bg-white border border-gray-200 overflow-hidden shadow-xs">
        <div className="p-5 border-b border-gray-200 bg-gray-50/70 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <div className="flex items-center space-x-2 text-xs font-mono uppercase tracking-wider text-brand-accent font-bold">
              <span>TRADING ECONOMICS PIPELINE</span>
            </div>
            <h3 className="font-serif text-lg font-bold text-brand-dark">
              {t('sec3_indicators_table_title')}
            </h3>
          </div>

          <a
            href="https://tradingeconomics.com/"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center text-xs font-mono text-brand-accent hover:underline gap-1 self-start sm:self-auto"
          >
            <span>{t('sec3_source_tradingecon')}</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead className="bg-gray-100 text-gray-600 uppercase tracking-wider text-[11px] border-b border-gray-200">
              <tr>
                <th className="py-3 px-4">Chỉ số kinh tế</th>
                <th className="py-3 px-4">Giá trị mới nhất</th>
                <th className="py-3 px-4">Kỳ trước</th>
                <th className="py-3 px-4">Tần suất</th>
                <th className="py-3 px-4">Thời điểm</th>
                <th className="py-3 px-4 text-right">Xu hướng</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data?.indicators.map((ind, idx) => {
                const isHighRisk = ind.code === 'CPI' && ind.value > 3.5;
                return (
                  <tr key={idx} className="hover:bg-gray-50/80 transition-colors">
                    <td className="py-3 px-4 font-sans font-semibold text-brand-dark text-xs sm:text-sm">
                      {ind.name}
                    </td>
                    <td className="py-3 px-4 font-bold text-sm">
                      <span className={isHighRisk ? 'text-red-700 bg-red-50 px-1.5 py-0.5 rounded' : 'text-brand-dark'}>
                        {ind.value} <span className="text-[11px] text-gray-500 font-normal">{ind.unit}</span>
                      </span>
                    </td>
                    <td className="py-3 px-4 text-gray-500">
                      {ind.previous} {ind.unit}
                    </td>
                    <td className="py-3 px-4 text-gray-500">
                      {ind.frequency}
                    </td>
                    <td className="py-3 px-4 text-gray-400 text-[11px]">
                      {ind.date}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {ind.trend === 'up' && (
                        <span className="text-red-600 inline-flex items-center text-[11px] font-bold">
                          ▲ Tăng
                        </span>
                      )}
                      {ind.trend === 'down' && (
                        <span className="text-emerald-600 inline-flex items-center text-[11px] font-bold">
                          ▼ Giảm
                        </span>
                      )}
                      {ind.trend === 'flat' && (
                        <span className="text-gray-400 inline-flex items-center text-[11px]">
                          ─ Bình ổn
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="p-3 bg-gray-50 border-t border-gray-200 text-[11px] font-mono text-gray-500 flex flex-col sm:flex-row justify-between items-center gap-2">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
            <span>Orchestration Status: Active • Connected with backend workflow</span>
          </div>
          <div>Cập nhật lúc: {data?.last_updated ? new Date(data.last_updated).toLocaleTimeString() : 'Vừa xong'}</div>
        </div>
      </div>
    </div>
  );
};
