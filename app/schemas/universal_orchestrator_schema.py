# ============================================================
# app/schemas/universal_orchestrator_schema.py
# Schema JSON phổ quát thay cho schema cứng theo từng lĩnh vực [DT §9]
# Mã giữ nguyên theo tài liệu nguồn («source code được cung cấp, giữ nguyên như cũ»).
# ============================================================
from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional, Literal

class DynamicRuleSpec(BaseModel):
    rule_id: str = Field(..., description="Law identifier in English snake_case")
    weight: float = Field(..., ge=0.01, le=0.99, description="ProbLog Probability Weight")
    conditions: List[str] = Field(..., description="List of conditional predicates")
    description_vi: str = Field(..., description="Explanation of the meaning of the law in Vietnamese")

class TaskOntologySchema(BaseModel):
    domain_tag: str = Field(..., description="Problem domain classification tag, e.g., 'macro_econ', 'distributed_sys'")
    allowed_predicates: List[str] = Field(..., description="List of valid predicates to be dynamically extracted for the problem")
    positive_rules: List[DynamicRuleSpec] = Field(default_factory=list)
    conflict_rules: List[DynamicRuleSpec] = Field(default_factory=list)

class IngestedClusterSchema(BaseModel):
    cluster_id: str
    crag_verification_state: Literal["CORRECT", "AMBIGUOUS", "INCORRECT"]
    numerical_divergence_warning: Dict[str, Any] = Field(default_factory=dict)
    structured_facts_summary: List[Dict[str, Any]]
    raw_excerpts: List[str]
    induced_ontology: TaskOntologySchema

class MetricBoundSpec(BaseModel):
    metric_name: str
    operator: Literal["<=", ">=", "==", "in_range"]
    threshold_value: float
    upper_bound: Optional[float] = None
    unit: str

class DualCritiqueReport(BaseModel):
    hypothesis_id: str
    practical_critic_model: str
    empirical_flaws: List[str]
    verified_external_evidence: List[str]
    theoretical_critic_model: str
    theoretical_risks: List[str]
    enforced_sandbox_bounds: List[MetricBoundSpec]
    prolog_scan_result: Dict[str, Any]

class UniversalSandboxProof(BaseModel):
    hypothesis_id: str
    repaired_mechanism_vi: str
    updated_facts: Dict[str, float]
    python_simulation_code: str = Field(
        ...,
        description="Python code must print to stdout the JSON string: {'computed_metrics': {...}, 'passed': true}"
    )

class FinalOrchestratorOutput(BaseModel):
    task_id: str
    domain_tag: str
    viable_pareto_frontier: List[Dict[str, Any]]
    rejected_dead_branches: List[Dict[str, Any]]
    newly_compiled_negative_rules: List[DynamicRuleSpec]
