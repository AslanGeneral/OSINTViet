import React from 'react';
import { PipelineMode } from '../../types';
import { useLanguage } from '../../context/LanguageContext';
import { Sliders, Cpu, Home, GitMerge, Sparkles } from 'lucide-react';

interface ModeSelectorProps {
  mode: PipelineMode;
  onChangeMode: (m: PipelineMode) => void;
}

export const ModeSelector: React.FC<ModeSelectorProps> = ({ mode, onChangeMode }) => {
  const { language } = useLanguage();

  const modes: Array<{
    id: PipelineMode;
    label: string;
    sublabel: string;
    icon: React.ReactNode;
    color: string;
    desc: string;
  }> = [
    {
      id: 'macro',
      label: 'Macro',
      sublabel: 'Engine v5.7',
      icon: <Cpu className="w-3.5 h-3.5" />,
      color: 'border-blue-500 text-blue-400',
      desc: language === 'vi' ? 'Chỉ chạy engine v5.7 vĩ mô, không ước lượng hộ gia đình.' : 'Runs macro engine v5.7 only without household micro estimation.',
    },
    {
      id: 'micro',
      label: 'Micro',
      sublabel: 'Household QUAIDS',
      icon: <Home className="w-3.5 h-3.5" />,
      color: 'border-emerald-500 text-emerald-400',
      desc: language === 'vi' ? 'Chỉ chạy engine hộ gia đình (QUAIDS, EGM, HANK).' : 'Runs household engine only (QUAIDS, Buffer Stock EGM, HANK).',
    },
    {
      id: 'dual',
      label: 'Dual-mode',
      sublabel: 'Macro → Micro',
      icon: <GitMerge className="w-3.5 h-3.5" />,
      color: 'border-purple-500 text-purple-400',
      desc: language === 'vi' ? 'Chạy macro trước, truyền payload đã ký sang micro.' : 'Sequential: Runs macro first, passes signed payload to micro.',
    },
    {
      id: 'auto',
      label: 'Auto',
      sublabel: 'Classifier AI',
      icon: <Sparkles className="w-3.5 h-3.5" />,
      color: 'border-amber-500 text-amber-400',
      desc: language === 'vi' ? 'Dùng bộ phân loại S2 tự động xác định chế độ phù hợp.' : 'Uses S2 classifier to automatically determine mode.',
    },
  ];

  return (
    <div className="p-3 bg-[#252526] text-gray-300 border-b border-[#3c3c3c]">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-mono uppercase tracking-wider text-gray-400 font-bold flex items-center gap-1.5">
          <Sliders className="w-3 h-3 text-brand-accent" />
          <span>CHẾ ĐỘ ORCHESTRATOR</span>
        </span>
        <span className="text-[10px] font-mono text-gray-500">Pipeline V2</span>
      </div>

      <div className="grid grid-cols-3 gap-1.5 mb-2">
        {modes.slice(0, 3).map((item) => {
          const isSelected = mode === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onChangeMode(item.id)}
              className={`p-1.5 text-center rounded text-xs font-mono transition-all flex flex-col items-center justify-center border ${
                isSelected
                  ? 'bg-[#37373d] text-white border-brand-accent font-bold shadow-xs ring-1 ring-brand-accent/50'
                  : 'bg-[#1e1e1e] text-gray-400 border-[#333333] hover:bg-[#2a2d2e] hover:text-gray-200'
              }`}
            >
              <div className="flex items-center gap-1 mb-0.5">
                {item.icon}
                <span className="font-semibold text-[11px]">{item.label}</span>
              </div>
              <span className="text-[9px] text-gray-500 leading-none">{item.sublabel}</span>
            </button>
          );
        })}
      </div>

      <div className="text-[10px] font-mono text-gray-400 bg-[#1e1e1e] p-1.5 rounded border border-[#333333] leading-tight">
        {modes.find((m) => m.id === mode)?.desc}
      </div>
    </div>
  );
};
