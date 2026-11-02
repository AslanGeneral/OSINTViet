import React, { useState } from 'react';
import { MacroBreakdownResponse, Article } from '../types';
import { useLanguage } from '../context/LanguageContext';
import { ParametersView } from '../components/parameters/ParametersView';
import { VSCodeLayout } from '../components/vscode/VSCodeLayout';
import { Terminal, Layers, Activity, Sliders, ShieldCheck } from 'lucide-react';

interface ToolViewProps {
  importedBaseData: { breakdown: MacroBreakdownResponse; article: Article } | null;
  onClearImportedData: () => void;
}

export const ToolView: React.FC<ToolViewProps> = ({
  importedBaseData,
  onClearImportedData,
}) => {
  const { language, t } = useLanguage();
  const [activeSubSection, setActiveSubSection] = useState<'parameters' | 'vscode'>('vscode');

  return (
    <div className="space-y-8 pb-16 font-sans">
      
      {/* Tool Header Navigation */}
      <section className="bg-white border-b border-gray-200 py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 border-b border-gray-100 pb-5">
            <div>
              <div className="flex items-center space-x-2 text-brand-accent text-xs font-mono uppercase tracking-widest font-bold mb-1">
                <Terminal className="w-3.5 h-3.5" />
                <span>MỤC III • HỆ THỐNG CÔNG CỤ ĐỊNH LƯỢNG (TOOL)</span>
              </div>
              <h1 className="font-serif text-3xl sm:text-4xl font-extrabold text-brand-dark tracking-tight">
                {t('sec3_title')}
              </h1>
              <p className="font-serif text-xs sm:text-sm text-gray-600 mt-1 max-w-2xl">
                Tích hợp dữ liệu thị trường toàn cầu (TradingEconomics) và môi trường phân tích neuro-symbolic Pipeline V2 theo giao diện VS Code chuyên nghiệp.
              </p>
            </div>

            {/* Sub-Section Toggle Buttons */}
            <div className="flex items-center p-1 bg-gray-100 border border-gray-300 rounded-xs self-start md:self-auto text-xs font-mono">
              <button
                type="button"
                onClick={() => setActiveSubSection('parameters')}
                className={`px-3 py-1.5 rounded-xs transition-all flex items-center gap-1.5 ${
                  activeSubSection === 'parameters'
                    ? 'bg-brand-accent text-white font-bold shadow-2xs'
                    : 'text-gray-700 hover:text-brand-dark'
                }`}
              >
                <Activity className="w-3.5 h-3.5" />
                <span>III.1: Parameters</span>
              </button>

              <button
                type="button"
                onClick={() => setActiveSubSection('vscode')}
                className={`px-3 py-1.5 rounded-xs transition-all flex items-center gap-1.5 ${
                  activeSubSection === 'vscode'
                    ? 'bg-brand-dark text-white font-bold shadow-2xs'
                    : 'text-gray-700 hover:text-brand-dark'
                }`}
              >
                <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                <span>III.2: VS Code Studio</span>
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Main Tool Content Pane */}
      <section className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {activeSubSection === 'parameters' ? (
          /* Subsection III.1: Parameters (Stocks & TradingEconomics) */
          <ParametersView />
        ) : (
          /* Subsection III.2: VS Code UI (Pipeline V2, Mode Selector, Chat & Center Output) */
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-serif text-xl font-bold text-brand-dark">
                  VS Code Quantitative Studio (Pipeline V2)
                </h3>
                <p className="text-xs text-gray-600 font-mono">
                  Tham chiếu: HUONG_DAN_PIPELINE_V2.md • Chat với LLM và nhập bối cảnh cá nhân để phân tích
                </p>
              </div>

              <div className="hidden sm:flex items-center gap-2 text-xs font-mono text-gray-500 bg-gray-50 px-3 py-1.5 border border-gray-200">
                <ShieldCheck className="w-4 h-4 text-emerald-600" />
                <span>HMAC Signed Output • Output trình bày chính giữa</span>
              </div>
            </div>

            <VSCodeLayout
              importedBaseData={importedBaseData}
              onClearImportedData={onClearImportedData}
            />
          </div>
        )}
      </section>

    </div>
  );
};
