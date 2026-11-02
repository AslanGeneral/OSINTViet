import React, { useState } from 'react';
import {
  PipelineMode,
  PipelineRunRequest,
  PipelineOutput,
  MacroBreakdownResponse,
  Article,
} from '../../types';
import { useLanguage } from '../../context/LanguageContext';
import { runPipelineV2 } from '../../services/api';
import { ModeSelector } from './ModeSelector';
import { ChatPanel } from './ChatPanel';
import { OutputViewer } from './OutputViewer';
import {
  Files,
  Play,
  Settings,
  GitBranch,
  Terminal,
  Activity,
  Layers,
  ShieldAlert,
  ChevronRight,
  ChevronDown,
  FileText,
  FileCode,
  HelpCircle,
} from 'lucide-react';

interface VSCodeLayoutProps {
  importedBaseData: { breakdown: MacroBreakdownResponse; article: Article } | null;
  onClearImportedData: () => void;
}

export const VSCodeLayout: React.FC<VSCodeLayoutProps> = ({
  importedBaseData,
  onClearImportedData,
}) => {
  const { language } = useLanguage();
  const [mode, setMode] = useState<PipelineMode>('dual');
  const [output, setOutput] = useState<PipelineOutput | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeActivity, setActiveActivity] = useState<'explorer' | 'pipeline' | 'settings'>('explorer');

  const handleRunPipeline = async (req: PipelineRunRequest) => {
    setLoading(true);
    try {
      const res = await runPipelineV2(req);
      setOutput(res);
    } catch (err: any) {
      console.error('Run pipeline error:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-[750px] bg-[#1e1e1e] border border-[#333333] rounded-sm overflow-hidden shadow-2xl font-mono text-xs">
      
      {/* VS Code Title Bar */}
      <div className="h-8 bg-[#323233] text-gray-400 flex items-center justify-between px-3 select-none border-b border-[#252526] text-[11px]">
        <div className="flex items-center space-x-2">
          <div className="flex space-x-1.5 mr-2">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f56]"></span>
            <span className="w-2.5 h-2.5 rounded-full bg-[#ffbd2e]"></span>
            <span className="w-2.5 h-2.5 rounded-full bg-[#27c93f]"></span>
          </div>
          <span className="text-gray-300 font-sans font-bold">OSINT Studio</span>
          <span>—</span>
          <span>pipeline_v2_orchestrator.py [HUONG_DAN_PIPELINE_V2.md]</span>
        </div>

        <div className="flex items-center space-x-3 text-[10px] text-gray-400">
          <span className="bg-[#1e1e1e] px-2 py-0.5 rounded text-gray-300 border border-[#3c3c3c]">
            Mode: {mode.toUpperCase()}
          </span>
          <span>Workspace: mirrored_backend</span>
        </div>
      </div>

      {/* Main Workspace: Activity Bar + Sidebar + Central Output Pane */}
      <div className="flex-1 flex overflow-hidden">
        
        {/* Leftmost Activity Bar */}
        <div className="w-12 bg-[#333333] flex flex-col items-center py-2 space-y-4 shrink-0 text-gray-400 select-none border-r border-[#252526]">
          <button
            onClick={() => setActiveActivity('explorer')}
            className={`p-2 rounded hover:text-white transition-colors relative ${
              activeActivity === 'explorer' ? 'text-white' : 'text-gray-400'
            }`}
            title="Explorer (Tập tin & Bối cảnh)"
          >
            <Files className="w-5 h-5" />
            {activeActivity === 'explorer' && (
              <span className="absolute left-0 top-1 bottom-1 w-0.5 bg-brand-accent"></span>
            )}
          </button>

          <button
            onClick={() => setActiveActivity('pipeline')}
            className={`p-2 rounded hover:text-white transition-colors relative ${
              activeActivity === 'pipeline' ? 'text-white' : 'text-gray-400'
            }`}
            title="Quy trình 7 tầng Pipeline V2"
          >
            <Layers className="w-5 h-5 text-purple-400" />
            {activeActivity === 'pipeline' && (
              <span className="absolute left-0 top-1 bottom-1 w-0.5 bg-brand-accent"></span>
            )}
          </button>

          <button
            onClick={() => setActiveActivity('settings')}
            className={`p-2 rounded hover:text-white transition-colors relative ${
              activeActivity === 'settings' ? 'text-white' : 'text-gray-400'
            }`}
            title="Cài đặt cấu hình"
          >
            <Settings className="w-5 h-5" />
            {activeActivity === 'settings' && (
              <span className="absolute left-0 top-1 bottom-1 w-0.5 bg-brand-accent"></span>
            )}
          </button>
        </div>

        {/* Sidebar Panel: Explorer / Chat & Context Input */}
        <div className="w-80 sm:w-96 bg-[#252526] border-r border-[#333333] flex flex-col shrink-0 overflow-hidden">
          
          {activeActivity === 'explorer' && (
            <>
              {/* Explorer Header */}
              <div className="p-2.5 text-[11px] font-bold text-gray-400 uppercase tracking-wider flex items-center justify-between border-b border-[#333333]">
                <span>EXPLORER: OSINT-PIPELINE-V2</span>
                <span className="text-[10px] text-gray-500 font-normal">S1 → S7</span>
              </div>

              {/* Mode Selector Header (Explicit user requirement) */}
              <ModeSelector mode={mode} onChangeMode={setMode} />

              {/* Chat & Household Context Panel */}
              <div className="flex-1 overflow-hidden">
                <ChatPanel
                  mode={mode}
                  onRunPipeline={handleRunPipeline}
                  loading={loading}
                  importedBaseData={importedBaseData}
                  onClearImportedData={onClearImportedData}
                />
              </div>
            </>
          )}

          {activeActivity === 'pipeline' && (
            <div className="p-4 space-y-4 text-xs font-mono overflow-y-auto text-gray-300">
              <div className="text-sm font-bold text-white border-b border-[#3c3c3c] pb-2">
                Kiến trúc Pipeline V2 (7 Tầng)
              </div>
              <div className="space-y-3">
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-blue-500 rounded-r">
                  <div className="font-bold text-blue-400">S1 – Input / LLM fetch</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">Nhận RawInput, phân rã truy vấn, bối cảnh cá nhân.</div>
                </div>
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-yellow-500 rounded-r">
                  <div className="font-bold text-yellow-400">S2 – Classifier</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">Lexicon tất định + prototype embedding.</div>
                </div>
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-gray-500 rounded-r">
                  <div className="font-bold text-gray-300">S3 – Cache Log</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">Ghi cache SQLite, bảo vệ bối cảnh cá nhân.</div>
                </div>
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-emerald-500 rounded-r">
                  <div className="font-bold text-emerald-400">S4 – Econ-Engine</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">Macro (v5.7) hoặc Micro (QUAIDS/HANK) hoặc Dual.</div>
                </div>
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-purple-500 rounded-r">
                  <div className="font-bold text-purple-400">S5 – Re-validation + Purge</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">Kiểm tra Grounding, Consistency, HMAC sandbox.</div>
                </div>
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-pink-500 rounded-r">
                  <div className="font-bold text-pink-400">S6 – Universal Orchestrator</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">Tournament CEGAR loop và phản biện đa tác tử.</div>
                </div>
                <div className="p-2.5 bg-[#1e1e1e] border-l-2 border-cyan-500 rounded-r">
                  <div className="font-bold text-cyan-400">S7 – Output Contract</div>
                  <div className="text-[11px] text-gray-400 mt-0.5">PipelineOutput JSON chuẩn hóa cho người dùng.</div>
                </div>
              </div>
            </div>
          )}

          {activeActivity === 'settings' && (
            <div className="p-4 space-y-3 text-xs font-mono text-gray-300">
              <div className="text-sm font-bold text-white border-b border-[#3c3c3c] pb-2">
                Cấu Hình Pipeline
              </div>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-gray-400">Dry-run Mode:</span>
                  <span className="text-emerald-400 font-bold">Tắt (Live run)</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">BYOK Router:</span>
                  <span className="text-gray-300">In-memory Isolated</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">LLM Engine:</span>
                  <span className="text-blue-400 font-bold">Local Ollama / Cloud</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-400">Vector Store:</span>
                  <span className="text-gray-300">ChromaDB 256-d MRL</span>
                </div>
              </div>
            </div>
          )}

        </div>

        {/* Central Main Editor Pane (OutputViewer - output trình bày ra chính giữa) */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <OutputViewer output={output} loading={loading} />
        </div>

      </div>

      {/* VS Code Bottom Status Bar */}
      <div className="h-6 bg-[#007acc] text-white flex items-center justify-between px-3 text-[11px] font-mono select-none shrink-0">
        <div className="flex items-center space-x-3">
          <span className="flex items-center gap-1 font-bold">
            <GitBranch className="w-3 h-3" />
            <span>main*</span>
          </span>
          <span className="hidden sm:inline">0 Errors, 0 Warnings</span>
          <span>•</span>
          <span className="uppercase">Mode: {mode}</span>
        </div>

        <div className="flex items-center space-x-3 text-[10px]">
          <span className="hidden sm:inline">UTF-8</span>
          <span className="hidden sm:inline">Python 3.12 (.venv)</span>
          <span className="bg-white/20 px-1.5 rounded">HMAC Attested</span>
        </div>
      </div>

    </div>
  );
};
