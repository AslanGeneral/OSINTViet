import React, { useState } from 'react';
import {
  PersonalContext,
  HouseholdProfile,
  PipelineMode,
  PipelineRunRequest,
  MacroBreakdownResponse,
  Article,
} from '../../types';
import { useLanguage } from '../../context/LanguageContext';
import {
  Send,
  User,
  Sliders,
  DollarSign,
  Users,
  ChevronDown,
  ChevronUp,
  Sparkles,
  Bookmark,
  CheckCircle2,
  RefreshCw,
} from 'lucide-react';

interface ChatPanelProps {
  mode: PipelineMode;
  onRunPipeline: (req: PipelineRunRequest) => void;
  loading: boolean;
  importedBaseData: { breakdown: MacroBreakdownResponse; article: Article } | null;
  onClearImportedData: () => void;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  mode,
  onRunPipeline,
  loading,
  importedBaseData,
  onClearImportedData,
}) => {
  const { language, t } = useLanguage();

  const [query, setQuery] = useState(
    'Lạm phát CPI, giá xăng dầu và lãi suất tác động đến ngân sách và chi tiêu gia đình tôi thế nào?'
  );

  // Household Profile state
  const [showHouseholdForm, setShowHouseholdForm] = useState(true);
  const [income, setIncome] = useState(25.0);
  const [expenditure, setExpenditure] = useState(19.0);
  const [nAdults, setNAdults] = useState(2);
  const [nChildren, setNChildren] = useState(1);
  const [liquidAssets, setLiquidAssets] = useState(45.0);
  const [debtService, setDebtService] = useState(4.5);
  const [foodShare, setFoodShare] = useState(0.42);
  const [energyShare, setEnergyShare] = useState(0.20);
  const [eduShare, setEduShare] = useState(0.18);
  const [otherShare, setOtherShare] = useState(0.20);

  // Personal context
  const [freeNotes, setFreeNotes] = useState('Hộ gia đình đô thị tại Hà Nội, có khoản vay mua nhà trả góp hàng tháng');

  const handleApplyPreset = (preset: 'urban' | 'trader' | 'worker') => {
    if (preset === 'urban') {
      setIncome(28.0);
      setExpenditure(21.0);
      setNAdults(2);
      setNChildren(2);
      setLiquidAssets(50.0);
      setDebtService(5.0);
      setFoodShare(0.40);
      setEnergyShare(0.18);
      setEduShare(0.22);
      setOtherShare(0.20);
      setFreeNotes('Gia đình công chức đô thị, 2 con nhỏ đang học tiểu học');
    } else if (preset === 'trader') {
      setIncome(35.0);
      setExpenditure(24.0);
      setNAdults(2);
      setNChildren(1);
      setLiquidAssets(80.0);
      setDebtService(6.5);
      setFoodShare(0.35);
      setEnergyShare(0.25);
      setEduShare(0.15);
      setOtherShare(0.25);
      setFreeNotes('Hộ kinh doanh buôn bán tự do, phụ thuộc giá xăng vận chuyển');
    } else {
      setIncome(18.0);
      setExpenditure(15.0);
      setNAdults(2);
      setNChildren(1);
      setLiquidAssets(20.0);
      setDebtService(2.0);
      setFoodShare(0.48);
      setEnergyShare(0.20);
      setEduShare(0.12);
      setOtherShare(0.20);
      setFreeNotes('Công nhân nhà máy, ngân sách ưu tiên chi phí ăn uống');
    }
  };

  const handleApplyImportedData = () => {
    if (importedBaseData) {
      setQuery(
        `Dựa trên phân tích bài viết "${importedBaseData.article.title}": ${importedBaseData.breakdown.original_macro_thesis}. Đánh giá tác động đến gia đình tôi.`
      );
    }
  };

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;

    // Normalize budget shares to sum to 1.0
    const totalShares = foodShare + energyShare + eduShare + otherShare;
    const normShares: [number, number, number, number] = [
      Number((foodShare / totalShares).toFixed(2)),
      Number((energyShare / totalShares).toFixed(2)),
      Number((eduShare / totalShares).toFixed(2)),
      Number((1 - (foodShare + energyShare + eduShare) / totalShares).toFixed(2)),
    ];

    const householdProfile: HouseholdProfile = {
      monthly_income_million_vnd: income,
      monthly_expenditure_million_vnd: expenditure,
      budget_shares_w0: normShares,
      n_adults: nAdults,
      n_children: nChildren,
      liquid_assets_million_vnd: liquidAssets,
      monthly_debt_service_million_vnd: debtService,
      crra_rho: 2.5,
    };

    const personalContext: PersonalContext = {
      free_text: freeNotes,
      region: 'Đô thị',
      household_notes: `${nAdults} người lớn, ${nChildren} trẻ em`,
      assets_liabilities_notes: `Nợ trả hàng tháng: ${debtService} triệu`,
      persist_opt_in: false,
    };

    onRunPipeline({
      query: query.trim(),
      mode,
      personal_context: personalContext,
      household_profile: householdProfile,
      allow_dual: true,
      dry_run: false,
    });
  };

  return (
    <div className="flex flex-col h-full bg-[#1e1e1e] text-gray-300 font-sans text-xs">
      {/* Imported Base Data Badge from Section II */}
      {importedBaseData && (
        <div className="p-2.5 bg-brand-accent/20 border-b border-brand-accent/40 text-brand-subtle flex flex-col gap-1.5 shrink-0">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-wider font-bold text-red-300 flex items-center gap-1">
              <Bookmark className="w-3 h-3 text-brand-accent" />
              <span>{t('vscode_handoff_notice')}</span>
            </span>
            <button
              onClick={onClearImportedData}
              className="text-gray-400 hover:text-white text-[10px] underline"
            >
              Hủy
            </button>
          </div>
          <div className="font-serif text-[11px] text-gray-200 line-clamp-2">
            "{importedBaseData.article.title}"
          </div>
          <button
            type="button"
            onClick={handleApplyImportedData}
            className="self-start text-[10px] font-mono font-bold bg-brand-accent hover:bg-brand-accent-hover text-white px-2 py-0.5 rounded transition-colors"
          >
            Nhập luận điểm này vào Query →
          </button>
        </div>
      )}

      {/* Scrollable Form Body */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        
        {/* Chat Query Prompt */}
        <div className="space-y-1.5">
          <label className="block text-[11px] font-mono text-gray-400 font-bold uppercase tracking-wider">
            {language === 'vi' ? 'Câu hỏi & Yêu cầu phân tích:' : 'Analysis Query:'}
          </label>
          <textarea
            rows={3}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('vscode_input_placeholder')}
            className="w-full bg-[#252526] border border-[#3c3c3c] focus:border-brand-accent focus:ring-1 focus:ring-brand-accent p-2 text-xs font-mono text-gray-200 rounded outline-none resize-none"
          />
        </div>

        {/* Quick Presets */}
        <div className="space-y-1">
          <span className="text-[10px] font-mono text-gray-500 uppercase tracking-wider">
            {language === 'vi' ? 'Mẫu hồ sơ hộ gia đình nhanh:' : 'Quick Household Presets:'}
          </span>
          <div className="flex flex-wrap gap-1 font-mono text-[10px]">
            <button
              type="button"
              onClick={() => handleApplyPreset('urban')}
              className="px-2 py-1 bg-[#2d2d30] hover:bg-[#3e3e42] text-gray-300 rounded border border-[#3c3c3c]"
            >
              🏢 Đô thị 2 con
            </button>
            <button
              type="button"
              onClick={() => handleApplyPreset('trader')}
              className="px-2 py-1 bg-[#2d2d30] hover:bg-[#3e3e42] text-gray-300 rounded border border-[#3c3c3c]"
            >
              🏪 Tiểu thương
            </button>
            <button
              type="button"
              onClick={() => handleApplyPreset('worker')}
              className="px-2 py-1 bg-[#2d2d30] hover:bg-[#3e3e42] text-gray-300 rounded border border-[#3c3c3c]"
            >
              🏭 Công nhân
            </button>
          </div>
        </div>

        {/* Structured Household Parameters Accordion */}
        <div className="border border-[#333333] rounded bg-[#252526] overflow-hidden">
          <button
            type="button"
            onClick={() => setShowHouseholdForm(!showHouseholdForm)}
            className="w-full p-2 bg-[#2d2d30] flex items-center justify-between text-left text-xs font-mono font-bold text-gray-300 hover:bg-[#37373d]"
          >
            <span className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-brand-accent" />
              <span>Hồ sơ Hộ Gia Đình (HouseholdProfile)</span>
            </span>
            {showHouseholdForm ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>

          {showHouseholdForm && (
            <div className="p-3 space-y-3 font-mono text-[11px]">
              
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-gray-400 block mb-0.5">Thu nhập (Tr VND):</label>
                  <input
                    type="number"
                    step="0.5"
                    value={income}
                    onChange={(e) => setIncome(parseFloat(e.target.value) || 0)}
                    className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                  />
                </div>
                <div>
                  <label className="text-gray-400 block mb-0.5">Chi tiêu (Tr VND):</label>
                  <input
                    type="number"
                    step="0.5"
                    value={expenditure}
                    onChange={(e) => setExpenditure(parseFloat(e.target.value) || 0)}
                    className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-gray-400 block mb-0.5">Số người lớn:</label>
                  <input
                    type="number"
                    min="1"
                    max="10"
                    value={nAdults}
                    onChange={(e) => setNAdults(parseInt(e.target.value) || 1)}
                    className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                  />
                </div>
                <div>
                  <label className="text-gray-400 block mb-0.5">Số trẻ em:</label>
                  <input
                    type="number"
                    min="0"
                    max="10"
                    value={nChildren}
                    onChange={(e) => setNChildren(parseInt(e.target.value) || 0)}
                    className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-gray-400 block mb-0.5">Quỹ tiền mặt (Tr VND):</label>
                  <input
                    type="number"
                    step="1"
                    value={liquidAssets}
                    onChange={(e) => setLiquidAssets(parseFloat(e.target.value) || 0)}
                    className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                  />
                </div>
                <div>
                  <label className="text-gray-400 block mb-0.5">Trả nợ/tháng (Tr VND):</label>
                  <input
                    type="number"
                    step="0.5"
                    value={debtService}
                    onChange={(e) => setDebtService(parseFloat(e.target.value) || 0)}
                    className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                  />
                </div>
              </div>

              {/* Budget Shares QUAIDS */}
              <div>
                <label className="text-gray-400 block mb-1">
                  Tỷ trọng ngân sách (QUAIDS w0, tổng = 1.0):
                </label>
                <div className="grid grid-cols-4 gap-1 text-[10px]">
                  <div>
                    <span className="text-gray-500">Ăn uống:</span>
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={foodShare}
                      onChange={(e) => setFoodShare(parseFloat(e.target.value) || 0)}
                      className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1 text-center text-gray-200 rounded"
                    />
                  </div>
                  <div>
                    <span className="text-gray-500">Xăng/Điện:</span>
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={energyShare}
                      onChange={(e) => setEnergyShare(parseFloat(e.target.value) || 0)}
                      className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1 text-center text-gray-200 rounded"
                    />
                  </div>
                  <div>
                    <span className="text-gray-500">Học phí:</span>
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={eduShare}
                      onChange={(e) => setEduShare(parseFloat(e.target.value) || 0)}
                      className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1 text-center text-gray-200 rounded"
                    />
                  </div>
                  <div>
                    <span className="text-gray-500">Khác:</span>
                    <input
                      type="number"
                      step="0.05"
                      min="0"
                      max="1"
                      value={otherShare}
                      onChange={(e) => setOtherShare(parseFloat(e.target.value) || 0)}
                      className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1 text-center text-gray-200 rounded"
                    />
                  </div>
                </div>
              </div>

              <div>
                <label className="text-gray-400 block mb-0.5">Ghi chú bối cảnh cá nhân:</label>
                <input
                  type="text"
                  value={freeNotes}
                  onChange={(e) => setFreeNotes(e.target.value)}
                  className="w-full bg-[#1e1e1e] border border-[#3c3c3c] p-1.5 text-gray-200 rounded text-xs"
                />
              </div>

            </div>
          )}
        </div>

      </div>

      {/* Action Footer Button */}
      <div className="p-3 bg-[#252526] border-t border-[#3c3c3c] shrink-0">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={loading || !query.trim()}
          className="w-full py-2.5 bg-brand-accent hover:bg-brand-accent-hover disabled:bg-gray-600 text-white font-mono text-xs font-bold uppercase tracking-wider rounded transition-colors flex items-center justify-center gap-2 shadow-xs"
        >
          {loading ? (
            <>
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              <span>Đang tính toán S1 → S7...</span>
            </>
          ) : (
            <>
              <span>{t('vscode_btn_run_pipeline')}</span>
            </>
          )}
        </button>
        <div className="text-[10px] font-mono text-gray-500 text-center mt-1.5">
          Quy trình 7 tầng: S1 Fetch → S2 Phân loại → S4 Engine → S5 Kiểm chứng ProbLog → S7 Output
        </div>
      </div>
    </div>
  );
};
