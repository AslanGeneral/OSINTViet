import React, { createContext, useContext, useState, ReactNode } from 'react';

export type Language = 'vi' | 'en';

interface Translations {
  [key: string]: {
    vi: string;
    en: string;
  };
}

const translations: Translations = {
  // Navigation
  nav_home: { vi: 'Trang chủ', en: 'Home' },
  nav_articles: { vi: 'Bài viết', en: 'Articles' },
  nav_tool: { vi: 'Công cụ (Tool)', en: 'Tool (IDE)' },
  nav_admin: { vi: 'Quản trị', en: 'Admin' },
  lang_switch_label: { vi: 'Ngôn ngữ', en: 'Language' },

  // Header / Brand
  brand_kicker: { vi: 'SÁNG KIẾN ĐỊNH LƯỢNG NGUỒN MỞ', en: 'OPEN-SOURCE QUANTITATIVE INITIATIVE' },
  brand_tagline: { vi: 'Chuyển hóa Vĩ mô thành Quyết sách Vi mô Hộ gia đình', en: 'Bridging Macroeconomic Shifts into Household Decisions' },

  // Section I: Home
  sec1_badge: { vi: 'TỔNG QUAN DỰ ÁN', en: 'PROJECT OVERVIEW' },
  sec1_project_desc_title: { vi: 'Nội dung dự án', en: 'Project Mission' },
  sec1_project_desc: {
    vi: 'Là công cụ định lượng nguồn mở, cung cấp giải pháp thân thiện và dễ hiểu cho người dùng cá nhân, với sự hỗ trợ của Trí tuệ Nhân tạo',
    en: 'An open-source quantitative tool providing user-friendly, accessible solutions for individual households, powered by Artificial Intelligence',
  },
  sec1_latest_articles_title: { vi: 'Bài Viết Mới Nhất', en: 'Latest Analysis' },
  sec1_latest_articles_desc: { vi: 'Các phân tích kinh tế vĩ mô và tác động vi mô được cập nhật liên tục', en: 'Continuous macroeconomic analyses and household impact assessments' },
  sec1_empty_articles: { vi: 'null (Chưa có bài viết nào trong hệ thống)', en: 'null (No articles found in the system)' },
  sec1_explore_more: { vi: 'Xem tất cả bài viết →', en: 'Explore all articles →' },
  sec1_open_tool_cta: { vi: 'Khởi chạy Tool Phân Tích (VS Code UI) →', en: 'Launch Analysis Tool (VS Code UI) →' },

  // Section II: Articles
  sec2_title: { vi: 'Bài Viết & Nhận Định Kinh Tế', en: 'Articles & Economic Insights' },
  sec2_desc: { vi: 'Chứa các bài viết phân tích chuyên sâu được trình bày theo phong cách báo điện tử học thuật', en: 'In-depth analytical essays curated in an academic digital newspaper style' },
  sec2_btn_ai_breakdown: { vi: '⚡ Phân tích Tác động AI', en: '⚡ AI Impact Breakdown' },
  sec2_read_more: { vi: 'Đọc toàn văn', en: 'Read full story' },
  sec2_filter_all: { vi: 'Tất cả chủ đề', en: 'All Topics' },
  sec2_filter_macro: { vi: 'Vĩ mô & Lạm phát', en: 'Macro & Inflation' },
  sec2_filter_energy: { vi: 'Xăng dầu & Năng lượng', en: 'Energy & Fuel' },
  sec2_filter_fx: { vi: 'Tỷ giá & Lãi suất', en: 'FX & Interest Rates' },

  // Left Drawer (Breakdown)
  drawer_title: { vi: 'Cửa Sổ Phân Tích & Breakdown Văn Bản', en: 'Text Analysis & Layman Breakdown' },
  drawer_subtitle: { vi: 'Chạy phân tích độc lập duy nhất trên nội dung bài viết này', en: 'Runs isolated NLP analysis strictly on this article content' },
  drawer_tab_breakdown: { vi: 'Breakdown Dễ Hiểu', en: 'Layman Breakdown' },
  drawer_tab_article: { vi: 'Nguyên Văn Bài Báo', en: 'Original Text' },
  drawer_macro_thesis: { vi: 'Luận Điểm Vĩ Mô Cốt Lõi', en: 'Core Macroeconomic Thesis' },
  drawer_layman_title: { vi: 'Giải Thích Dễ Hiểu (Cấp 2)', en: 'Middle-School Layman Explanation' },
  drawer_household_impacts: { vi: 'Tác Động Cụ Thể Lên Ngân Sách Hộ Gia Đình', en: 'Concrete Household Budget Impacts' },
  drawer_recommended_actions: { vi: '3 Hành Động Hộ Gia Đình Nên Làm Ngay', en: '3 Recommended Immediate Household Actions' },
  drawer_confidence: { vi: 'Độ tin cậy mô hình', en: 'Model Confidence' },
  drawer_btn_handoff: {
    vi: 'Sử dụng thông tin này làm base dữ liệu cho Tool (Mục III) →',
    en: 'Use this data as base context for Tool (Section III) →',
  },
  drawer_resize_tip: { vi: 'Kéo mép phải để mở rộng / thu hẹp cửa sổ', en: 'Drag right edge to resize drawer width' },

  // Section III: Tool
  sec3_title: { vi: 'Công Cụ Phân Tích & Giả Lập (Tool)', en: 'Analysis & Simulation Tool' },
  sec3_tab_parameters: { vi: 'Mục III.1: Parameters (Chỉ số & Thị trường)', en: 'Section III.1: Parameters (Markets & Indices)' },
  sec3_tab_vscode: { vi: 'Mục III.2: VS Code Studio (Workflow V2)', en: 'Section III.2: VS Code Studio (Workflow V2)' },

  // III.1 Parameters
  sec3_params_title: { vi: 'Dữ Liệu Chứng Khoán & Chỉ Số Kinh Tế', en: 'Stock Markets & Economic Indicators' },
  sec3_params_desc: { vi: 'Sử dụng Headless Browser Playwright cào trực tiếp từ TradingEconomics và các sàn chứng khoán, orchestrate với backend.', en: 'Powered by headless Playwright browser scraping TradingEconomics and global stock exchanges, orchestrated with backend.' },
  sec3_country_selector_label: { vi: 'Chọn quốc gia hiển thị:', en: 'Select country to view metrics:' },
  sec3_stocks_table_title: { vi: 'Chỉ Số Chứng Khoán', en: 'Stock Market Indices' },
  sec3_indicators_table_title: { vi: 'Chỉ Số Kinh Tế Vĩ Mô (TradingEconomics)', en: 'Macroeconomic Indicators (TradingEconomics)' },
  sec3_btn_refresh_scrape: { vi: 'Làm Mới Live (Playwright Headless)', en: 'Refresh Live (Playwright Headless)' },
  sec3_source_tradingecon: { vi: 'Nguồn: https://tradingeconomics.com/', en: 'Source: https://tradingeconomics.com/' },

  // III.2 VS Code UI
  vscode_explorer: { vi: 'EXPLORER', en: 'EXPLORER' },
  vscode_files: { vi: 'TẬP TIN PIPELINE V2', en: 'PIPELINE V2 FILES' },
  vscode_mode_label: { vi: 'Chế độ Orchestrator:', en: 'Orchestrator Mode:' },
  vscode_chat_title: { vi: 'Trò Chuyện & Bối Cảnh Bản Thân', en: 'Chat & Personal Context' },
  vscode_chat_desc: { vi: 'Nhập câu hỏi hoặc bối cảnh tài chính cá nhân để AI phân tích và đưa output ra chính giữa.', en: 'Input queries or personal household context to analyze and render output in the center pane.' },
  vscode_input_placeholder: { vi: 'Nhập câu hỏi hoặc bối cảnh (Ví dụ: Lương 25tr, nợ mua nhà 8tr, 1 con nhỏ... lạm phát xăng dầu ảnh hưởng tôi thế nào?)', en: 'Enter question or context (e.g. Income $1,200/mo, mortgage debt, 1 kid... how does inflation affect me?)' },
  vscode_btn_run_pipeline: { vi: '▶ Chạy Phân Tích Pipeline V2', en: '▶ Run Pipeline V2 Analysis' },
  vscode_handoff_notice: { vi: 'Đã nhận dữ liệu base từ bài báo Mục II', en: 'Base data imported from Section II article' },
  vscode_editor_title: { vi: 'OUTPUT PHÂN TÍCH CHÍNH GIỮA', en: 'CENTRAL OUTPUT EDITOR' },
  vscode_tab_report: { vi: 'analysis_report.md', en: 'analysis_report.md' },
  vscode_tab_budget: { vi: 'household_budget.json', en: 'household_budget.json' },
  vscode_tab_audit: { vi: 'revalidation_audit.log', en: 'revalidation_audit.log' },
  vscode_tab_raw: { vi: 'pipeline_output.json', en: 'pipeline_output.json' },

  // Section IV: Admin
  sec4_title: { vi: 'Cổng Quản Trị Hệ Thống (Admin Portal)', en: 'System Administration Portal' },
  sec4_desc: { vi: 'Module quản trị độc lập (thiết kế sẵn sàng cho subdomain admin.xxx.yyy/only)', en: 'Isolated management module (designed ready for subdomain admin.xxx.yyy/only)' },
  sec4_tab_publish: { vi: 'IV.1 Đăng bài viết mới', en: 'IV.1 Publish Article' },
  sec4_tab_system: { vi: 'IV.2 Token, Test API & Lỗi', en: 'IV.2 Tokens, API Test & Logs' },
  sec4_tab_customize: { vi: 'IV.3 Sửa Text & Tính Năng Web', en: 'IV.3 Edit Text & Features' },
  sec4_auth_title: { vi: 'Xác Thực Quyền Admin', en: 'Admin Passcode Required' },
  sec4_auth_desc: { vi: 'Nhập mã quản trị để truy cập cấu hình hệ thống và xuất bản bài viết.', en: 'Enter administrator passcode to access system telemetry and publishing.' },
  sec4_btn_login: { vi: 'Mở Khóa Quản Trị', en: 'Unlock Admin Portal' },
};

interface LanguageContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string) => string;
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined);

export const LanguageProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [language, setLanguage] = useState<Language>('vi');

  const t = (key: string): string => {
    if (translations[key]) {
      return translations[key][language] || translations[key].vi;
    }
    return key;
  };

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  );
};

export const useLanguage = (): LanguageContextType => {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error('useLanguage must be used within a LanguageProvider');
  }
  return context;
};
