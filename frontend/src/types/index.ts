export interface Article {
  id: string;
  title: string;
  source: string;
  url: string;
  thumbnail: string;
  chapeau: string;
  content: string;
  relevance_score?: number;
  domain_tag?: string;
  ingested_at?: string;
}

export interface HouseholdImpact {
  category: string;
  expected_change_pct: number;
  layman_explanation: string;
}

export interface MacroBreakdownResponse {
  article_title: string;
  original_macro_thesis: string;
  layman_summary: string;
  micro_impacts: HouseholdImpact[];
  recommended_actions: string[];
  confidence_score: number;
}

export interface StockIndex {
  symbol: string;
  name: string;
  value: number;
  change: number;
  pct_change: number;
  high: number;
  low: number;
  trend: 'up' | 'down' | 'flat';
}

export interface EconomicIndicator {
  name: string;
  code: string;
  value: number;
  unit: string;
  previous: number;
  frequency: string;
  date: string;
  trend: 'up' | 'down' | 'flat';
}

export interface ParametersData {
  country_key: string;
  country_name: string;
  currency: string;
  stocks: StockIndex[];
  indicators: EconomicIndicator[];
  source: string;
  orchestrated: boolean;
  engine: string;
  last_updated: string;
}

export interface HouseholdProfile {
  monthly_income_million_vnd?: number;
  monthly_expenditure_million_vnd?: number;
  budget_shares_w0?: [number, number, number, number]; // [food, energy, tuition/edu, other]
  n_adults?: number;
  n_children?: number;
  liquid_assets_million_vnd?: number;
  illiquid_assets_million_vnd?: number;
  monthly_debt_service_million_vnd?: number;
  crra_rho?: number;
}

export interface PersonalContext {
  free_text?: string;
  region?: string;
  occupation_sector?: string;
  household_notes?: string;
  assets_liabilities_notes?: string;
  persist_opt_in?: boolean;
}

export type PipelineMode = 'macro' | 'micro' | 'dual' | 'auto';

export interface PipelineRunRequest {
  query: string;
  mode: PipelineMode;
  personal_context?: PersonalContext;
  household_profile?: HouseholdProfile;
  allow_dual?: boolean;
  dry_run?: boolean;
  use_live?: boolean;
  enable_orchestrator?: boolean;
  model?: string;
}

export interface PipelineOutput {
  run_id?: string;
  mode?: string;
  macro?: {
    thesis_summary?: string;
    headline_metrics?: Record<string, any>;
    claims?: Array<{ claim: string; support: string }>;
  };
  micro?: {
    coli_index?: number;
    compensating_variation_million_vnd?: number;
    buffer_stock_adequacy_months?: number;
    debt_service_ratio?: number;
    channels?: Record<string, any>;
  };
  revalidation?: {
    grounding_passed?: boolean;
    consistency_passed?: boolean;
    completeness_passed?: boolean;
    mode_integrity_passed?: boolean;
  };
  audit_signature?: string;
  timestamp?: string;
}

export interface SiteConfig {
  site_title: string;
  project_name: string;
  tagline: string;
  project_description: string;
  project_description_en: string;
  announcement: string;
  announcement_en?: string;
  auto_fetch_interval_hours: number;
  enable_live_scraper: boolean;
  enable_dual_mode: boolean;
  enable_dark_mode: boolean;
  show_ticker: boolean;
  custom_notice?: string;
}

export interface SystemStats {
  database: {
    articles_count: number;
    workflow_runs_count: number;
    cache_audits_count: number;
    status: string;
  };
  tokens: {
    gemini: string;
    cloud_openai: string;
    cohere: string;
    admin_passcode: string;
  };
  llm_engine: {
    provider: string;
    local_ollama_url: string;
    local_model: string;
    cloud_model: string;
  };
  embedding: Record<string, any>;
  site_config: SiteConfig;
  timestamp: string;
}
