"""Subsystem equations for CLIO-CUSP 8192 v4.0.

Every function here is a pure transform of JSON-native inputs.  Numerical
primitives that are part of the locked Section 10 kernel (Wiener virality,
Katz-Bonacich centrality, severity, weather, Hawkes, latent discontent,
penalty ladder, coup probabilities) are called through ``clio_cusp.kernel``
rather than reimplemented.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import scipy.sparse as sp

from app.math_engine.clio_cusp.constants import (
    AGITATED_CONTEXTS,
    AGITATED_EVENT_TYPES,
    PLOVER_EVENT_TYPES,
    PLOVER_GOLDSTEIN_BASE,
    PLOVER_MODE_WEIGHTS,
)
from app.math_engine.clio_cusp.contracts import (
    CLIOExogenous,
    EdgeInput,
    EventInput,
    InterestGroupInput,
    SignedNetworkInput,
)
from app.math_engine.clio_cusp.kernel import (
    K_EPSTEIN,
    T_ACT_EPSTEIN,
    compute_asi_outcome,
    compute_coup_attempt_logit,
    compute_emp,
    compute_military_fragmentation,
    compute_mmp_aug,
    compute_power_metrics,
    compute_rdi,
    compute_sclerosis,
    compute_severity_score,
    compute_sfd,
    compute_smr,
    compute_social_amplification,
    compute_structural_virality_o_n,
    compute_weather_modulators,
    coup_probabilities,
    crowd_size_weight,
    granovetter_fold,
    penalty_increment as kernel_penalty_increment,
    update_accumulated_bias,
    update_hawkes_state,
    update_latent_discontent,
    update_penalty_state,
    update_psi,
)


# ---------------------------------------------------------------------------
# Subsystem 4: event taxonomy, severity, virality and penalty ladder
# ---------------------------------------------------------------------------


def _canonical_type(value: str) -> str:
    key = str(value or "").strip().upper().replace(" ", "_")
    if key in PLOVER_GOLDSTEIN_BASE:
        return key
    raise ValueError(
        f"unknown PLOVER event type {value!r}; expected one of "
        f"{', '.join(PLOVER_EVENT_TYPES)}"
    )


def plover_goldstein(event: EventInput) -> float:
    """Compute G_PLOVER,j from classifier probabilities and the Mode field.

    If the caller supplies ``goldstein_expectation`` it is treated as the
    already-aggregated ConfliBERT-2 score (including Mode); otherwise the
    probability vector is normalized and combined with the root weights.
    """
    if event.goldstein_expectation is not None:
        return float(np.clip(event.goldstein_expectation, -10.0, 8.3))

    if event.probabilities:
        total = float(sum(event.probabilities.values()))
        if total <= 0:
            raise ValueError("event probabilities must have positive mass")
        score = 0.0
        for label, probability in event.probabilities.items():
            canonical = _canonical_type(label)
            score += (float(probability) / total) * PLOVER_GOLDSTEIN_BASE[canonical]
    else:
        canonical = _canonical_type(event.event_type)
        score = PLOVER_GOLDSTEIN_BASE[canonical]

    mode = str(event.mode or "").strip().lower()
    score += PLOVER_MODE_WEIGHTS.get(mode, 0.0)
    return float(np.clip(score, -10.0, 8.3))


def classify_agitated(event: EventInput) -> bool:
    """PLOVER agitated anti-regime classification from Type/Mode/Context."""
    if event.is_agitated is not None:
        return bool(event.is_agitated)
    event_type = str(event.event_type or "").strip().upper().replace(" ", "_")
    if event_type not in AGITATED_EVENT_TYPES:
        return False
    context = str(event.context or "").strip().lower()
    mode = str(event.mode or "").strip().lower()
    return context in AGITATED_CONTEXTS or mode == "riot_violent"


def _parents(event: EventInput) -> List[int]:
    if event.parent_indices:
        return list(event.parent_indices)
    return [-1]


def event_severity(event: EventInput, goldstein: float, size_weight: float,
                   baseline_loss: float) -> float:
    if goldstein >= 0.0:
        return 0.0
    return compute_severity_score(
        goldstein_expectation=goldstein,
        size_weight=size_weight,
        damage=float(event.damage),
        baseline_loss=baseline_loss,
    )


def event_virality(event: EventInput, severity: float, alpha_social: float) -> Tuple[float, float, float]:
    """Return (structural_virality, social_amplification, negative_score)."""
    parents = _parents(event)
    nu = compute_structural_virality_o_n(parents)
    negative_score = (
        float(event.negative_event_score)
        if event.negative_event_score is not None
        else float(np.clip(max(0.0, severity), 0.0, 1.0))
    )
    amplification = compute_social_amplification(
        negative_event_score=negative_score,
        new_shares_per_day=float(event.new_shares_per_day),
        virality=nu,
        alpha_social=alpha_social,
    )
    return nu, amplification, negative_score


def event_layer(
    events: Sequence[EventInput],
    day: int,
    params: CLIOExogenous,
    sclerosis: float,
    omega_total: float,
    previous_penalty: float,
    previous_bias: float,
    penalty_cpt: float,
) -> Dict[str, Any]:
    """Apply the daily event stream and return the updated penalty state."""
    active = [event for event in events if int(event.day) == int(day)]
    records: List[Dict[str, Any]] = []
    increments: List[float] = []
    total_impact = 0.0

    for event in active:
        goldstein = plover_goldstein(event)
        weight = crowd_size_weight(event.headcount)
        severity = event_severity(
            event, goldstein, weight, params.events.baseline_loss
        )
        virality, amplification, negative_score = event_virality(
            event, severity, params.events.alpha_social
        )
        impact = float(np.clip(severity * (1.0 + amplification), 0.0, 2.0))
        increment = kernel_penalty_increment(impact) if impact > 0.0 else 0.0
        if increment > 0.0:
            increments.append(increment)
        total_impact += impact
        records.append(
            {
                "event_id": event.event_id,
                "event_type": str(event.event_type),
                "mode": str(event.mode),
                "context": str(event.context),
                "agitated": classify_agitated(event),
                "goldstein_plover": round(goldstein, 8),
                "size_weight": round(weight, 8),
                "severity": round(severity, 8),
                "virality": round(virality, 8),
                "impact": round(impact, 8),
                "penalty_increment": round(increment, 10),
                "social_amplification": round(amplification, 8),
            }
        )

    # Section 4.3 defines the interest-group penalty channel
    # Delta P_IG,t^+ = Omega_total,t * 10^-2.
    ig_increment = max(0.0, float(omega_total)) * 1e-2
    if ig_increment > 0.0:
        increments.append(ig_increment)

    remediation = [
        (float(item.amount), float(item.age_days)) for item in params.events.remediation
    ]
    penalty, bias = update_penalty_state(
        previous=previous_penalty,
        positive_increments=increments,
        remediation=remediation,
        sclerosis=sclerosis,
        delay_rate=params.events.delay_rate,
        accumulated_bias=previous_bias,
        lambda_cpt=penalty_cpt,
    )
    # A one-day publication delay activates the prospect-theory bias floor.
    delayed = [(value, 1.0) for value in increments]
    bias = update_accumulated_bias(
        previous=previous_bias,
        delayed_increments=delayed,
        theta_bias=params.events.theta_bias,
        delay_rate=params.events.delay_rate,
    )
    penalty = min(1.0, max(bias, penalty))

    if params.events.spi_override is not None:
        spi = float(params.events.spi_override)
    else:
        spi = float(1.0 - math.exp(-max(0.0, total_impact)))

    return {
        "events": records,
        "event_count": len(active),
        "penalty": penalty,
        "bias": bias,
        "spi": spi,
        "event_penalty_total": float(sum(increments)),
        "impact_total": total_impact,
    }


# ---------------------------------------------------------------------------
# Subsystem 5: Epstein activation and ESIM conversion
# ---------------------------------------------------------------------------


def epstein_activation_rate(
    penalty: float,
    police_count: float,
    active_protesters: float,
    agents: int = 1024,
    h_sigma: float = 0.30,
    r_sigma: float = 0.30,
) -> float:
    """Share of a deterministic heterogeneous agent grid that activates.

    G_i = H_i P_t and N_i = R_i [1-exp(-k floor(C_cop/(A_active+1)))].
    H_i and R_i are log-normal quantiles so the grid is fixed and auditable.
    """
    if agents <= 0:
        raise ValueError("agents must be positive")
    ratio = math.floor(max(0.0, police_count) / (max(0.0, active_protesters) + 1.0))
    arrest_probability = 1.0 - math.exp(-K_EPSTEIN * ratio)
    quantiles = (np.arange(agents, dtype=np.float64) + 0.5) / agents
    from scipy.special import ndtri

    h = np.exp(-0.5 * h_sigma**2 + h_sigma * ndtri(quantiles))
    r = np.exp(-0.5 * r_sigma**2 + r_sigma * ndtri(quantiles))
    grievance = h * max(0.0, penalty)
    net_risk = r * arrest_probability
    return float(np.mean(grievance - net_risk > T_ACT_EPSTEIN))


def esim_conversion(params: CLIOExogenous) -> Tuple[float, float]:
    """Return (normalized share converted, raw agent count).

    Section 7.1 states Delta N = gamma F (1-eta) N_pol.  The cusp equation
    needs a dimensionless split, so the normalized value divides by N_pol,
    leaving gamma F (1-eta); the raw count is retained for audit.
    """
    e = params.events
    normalized = float(
        np.clip(e.gamma_esim * e.coercive_intensity * (1.0 - e.eta_select), 0.0, 1.0)
    )
    raw = normalized * float(e.active_protesters)
    return normalized, raw


# ---------------------------------------------------------------------------
# Subsystem 2: signed network and Omega_r,t
# ---------------------------------------------------------------------------


def _edge_matrix(edges: Iterable[EdgeInput], n_nodes: int) -> sp.csr_matrix:
    rows: List[int] = []
    cols: List[int] = []
    values: List[float] = []
    for edge in edges:
        if edge.source >= n_nodes or edge.target >= n_nodes:
            raise ValueError("edge endpoint is outside the network")
        if edge.weight <= 0:
            continue
        rows.append(edge.source)
        cols.append(edge.target)
        values.append(float(edge.weight))
    return sp.csr_matrix(
        (values, (rows, cols)), shape=(n_nodes, n_nodes), dtype=np.float64
    )


def resolve_network(params: CLIOExogenous) -> Tuple[sp.csr_matrix, sp.csr_matrix, List[int]]:
    """Build the two adjacency layers, generating a transparent default.

    With no explicit network, one state node is connected to one node per
    interest group: a positive edge for state-affiliated groups and a negative
    edge otherwise.  This is the minimal graph that still exercises the signed
    CPM/Katz formulas; callers should supply a measured network for production.
    """
    power = params.power
    explicit = power.network
    if explicit is not None and explicit.n_nodes > 0:
        n_nodes = explicit.n_nodes
        positive = _edge_matrix(explicit.positive_edges, n_nodes)
        negative = _edge_matrix(explicit.negative_edges, n_nodes)
        state_nodes = list(explicit.state_nodes)
        if not state_nodes:
            state_nodes = list(range(min(1, n_nodes)))
        return positive, negative, state_nodes

    groups = power.interest_groups
    # Two state nodes keep the denominator of Omega_r,t away from the
    # eps-floor when the opposition layer is sparse.  Node 0 is the regime
    # core and node 1 is the state apparatus; groups follow.
    n_nodes = 2 + len(groups)
    positive_edges: List[EdgeInput] = [
        EdgeInput(source=0, target=1, weight=1.0),
        EdgeInput(source=1, target=0, weight=1.0),
    ]
    negative_edges: List[EdgeInput] = []
    for index, group in enumerate(groups):
        node = index + 2
        edge = EdgeInput(source=0, target=node, weight=1.0)
        if group.state_affiliated:
            positive_edges.append(edge)
        else:
            negative_edges.append(edge)
    positive = _edge_matrix(positive_edges, n_nodes)
    negative = _edge_matrix(negative_edges, n_nodes)
    return positive, negative, [0, 1]


def _group_hostility(
    group: InterestGroupInput,
    negative: sp.csr_matrix,
    positive: sp.csr_matrix,
    state_nodes: Sequence[int],
) -> Tuple[float, float]:
    nodes = [node for node in group.node_indices if node < negative.shape[0]]
    if not nodes or not state_nodes:
        return 0.0, 0.0
    hostility = float(negative[nodes][:, list(state_nodes)].sum())
    alliance = float(positive[nodes][:, list(state_nodes)].sum())
    return hostility, alliance


def interest_group_magnitudes(
    params: CLIOExogenous,
    centrality: np.ndarray,
) -> Tuple[float, List[Dict[str, Any]], List[float]]:
    """Compute Omega_r,t for each oppositional group and Omega_total."""
    power = params.power
    positive, negative, state_nodes = resolve_network(params)
    centrality = np.asarray(centrality, dtype=np.float64)
    state_strength = float(centrality[state_nodes].sum()) if state_nodes else 1e-9
    state_strength = max(state_strength, 1e-9)
    total_assets = float(sum(group.assets for group in power.interest_groups))
    total_media = float(sum(group.media_virality for group in power.interest_groups))

    rows: List[Dict[str, Any]] = []
    omegas: List[float] = []
    weights = power.omega_weights
    for group in power.interest_groups:
        hostility, alliance = _group_hostility(
            group, negative, positive, state_nodes
        )
        if group.state_affiliated:
            oppositional = False
        elif group.oppositional is not None:
            oppositional = bool(group.oppositional)
        else:
            oppositional = (
                hostility > alliance
                and hostility >= power.opposition_threshold
            )
        nodes = [node for node in group.node_indices if node < centrality.size]
        group_strength = float(centrality[nodes].sum()) if nodes else 0.0
        asset_share = group.assets / total_assets if total_assets > 0 else 0.0
        media_share = group.media_virality / total_media if total_media > 0 else 0.0
        omega = 0.0
        if oppositional:
            omega = (
                weights.assets * asset_share
                + weights.centrality * (group_strength / state_strength)
                + weights.media * media_share
            )
            omegas.append(float(omega))
        rows.append(
            {
                "group_id": group.group_id,
                "oppositional": oppositional,
                "hostility": round(hostility, 8),
                "alliance": round(alliance, 8),
                "asset_share": round(asset_share, 8),
                "centrality_share": round(group_strength / state_strength, 8),
                "media_share": round(media_share, 8),
                "omega": round(float(omega), 8),
            }
        )
    return float(sum(omegas)), rows, omegas


# ---------------------------------------------------------------------------
# Subsystem 1 and 3 snapshots
# ---------------------------------------------------------------------------


def structural_layer(
    params: CLIOExogenous,
    sclerosis: float,
    previous_psi: float,
    rdi_override: Optional[float] = None,
) -> Dict[str, float]:
    s = params.structural
    mmp, auto_ineq = compute_mmp_aug(
        gdp_pc=s.gdp_pc,
        median_wage=s.median_wage,
        urban_share=s.urban_share,
        youth_share=s.youth_share,
        top10_share=s.top10_share,
        bottom50_share=s.bottom50_share,
        productivity_growth=s.productivity_growth,
        median_wage_growth=s.median_wage_growth,
        omega_gini=s.omega_gini,
        omega_tech=s.omega_tech,
    )
    emp = compute_emp(
        elite_share=s.elite_share,
        labor_share=s.labor_share,
        top1_share=s.top1_share,
        top10_share=s.top10_share,
    )
    sfd = compute_sfd(
        public_debt_gdp=s.public_debt_gdp,
        interest_plus_deficit_tax=s.interest_plus_deficit_tax,
        w_debt=s.w_debt,
        w_def=s.w_def,
    )
    rdi = (
        float(rdi_override)
        if rdi_override is not None
        else compute_rdi(s.expected_value, s.capability_value)
    )
    psi = update_psi(
        psi_prev=previous_psi,
        mmp_aug=mmp,
        emp=emp,
        sfd=sfd,
        sclerosis=sclerosis,
        reform=s.reform,
        delta_psi=s.delta_psi,
        eta_reform=s.eta_reform,
    )
    return {
        "mmp_aug": mmp,
        "auto_ineq": auto_ineq,
        "emp": emp,
        "sfd": sfd,
        "rdi": rdi,
        "psi": psi,
    }


def power_layer(params: CLIOExogenous, previous_psi: float) -> Dict[str, Any]:
    p = params.power
    metrics = compute_power_metrics(
        shares=p.faction_shares,
        selectorate=p.selectorate,
        coalition=p.coalition,
    )
    sclerosis = compute_sclerosis(
        lambda_olson=p.lambda_olson,
        stability_duration=p.stability_duration,
        veto_players=p.veto_players,
        rent_gdp=p.rent_gdp,
    )
    positive, negative, state_nodes = resolve_network(params)
    if positive.shape[0] == 0:
        centrality = np.empty(0, dtype=np.float64)
    else:
        from app.math_engine.clio_cusp.kernel import (
            compute_signed_bilinear_katz_bonacich,
        )

        centrality = compute_signed_bilinear_katz_bonacich(
            positive,
            negative,
            omega_conflict=p.lambda_conflict,
            eps_floor=p.omega_eps_floor,
        )
    omega_total, groups, omegas = interest_group_magnitudes(params, centrality)
    return {
        **metrics,
        "sclerosis": sclerosis,
        "omega_total": omega_total,
        "omega_groups": omegas,
        "interest_groups": groups,
        "network_nodes": int(positive.shape[0]),
        "network_edges_positive": int(positive.nnz),
        "network_edges_negative": int(negative.nnz),
        "state_nodes": state_nodes,
    }


def military_layer(
    params: CLIOExogenous,
    emp: float,
    penalty: float,
    delta_f_mil_365: float,
    harbingers: Sequence[float],
    coeffs: Any,
) -> Dict[str, float]:
    m = params.military
    smr = compute_smr(
        active_military=m.active_military,
        paramilitary=m.paramilitary,
        reserve=m.reserve,
        population=m.population,
        military_expenditure=m.military_expenditure,
        gdp=m.gdp,
        active_max=m.active_max,
        reserve_max=m.reserve_max,
        expenditure_max=m.expenditure_max,
    )
    fragmentation = compute_military_fragmentation(m.military_shares)
    total_force = (
        float(m.military_total)
        if m.military_total is not None
        else float(m.active_military + m.paramilitary + m.reserve)
    )
    total_force = max(total_force, 1e-9)
    asi = compute_asi_outcome(
        fragmentation=fragmentation,
        military_expenditure=m.military_expenditure,
        military_total=total_force,
        baseline_pay=m.baseline_pay,
        selectorate=params.power.selectorate,
        coalition=params.power.coalition,
        eta_coup=m.eta_coup,
    )
    z_attempt = compute_coup_attempt_logit(
        intercept=coeffs.intercept,
        penalty=penalty,
        emp=emp,
        gdp_pc_log_growth=params.structural.gdp_pc_growth_daily * 365.0,
        fragmentation_change_365=delta_f_mil_365,
        harbingers=harbingers,
        beta_penalty=coeffs.beta_penalty,
        beta_emp=coeffs.beta_emp,
        beta_gdp=coeffs.beta_gdp,
        beta_preempt=coeffs.beta_preempt,
        gamma=coeffs.gamma_harbingers,
    )
    probabilities = coup_probabilities(
        z_attempt=z_attempt,
        asi_outcome=asi,
        penalty=penalty,
        elite_defection=float(harbingers[2]) if len(harbingers) > 2 else 0.0,
        alpha_def=coeffs.alpha_def,
        alpha_legit=coeffs.alpha_legit,
        alpha_defection=coeffs.alpha_defection,
    )
    return {
        "smr": smr,
        "f_mil": fragmentation,
        "asi_outcome": asi,
        "z_attempt": z_attempt,
        "p_attempt": probabilities["attempt"],
        "p_success": probabilities["success_given_attempt"],
        "p_regime_change": probabilities["regime_change"],
        "p_bloody_failed": probabilities["bloody_failed"],
    }


def psychology_layer(
    params: CLIOExogenous,
    penalty: float,
    rdi: float,
    delta_penalty: float,
    agitation_events: Sequence[float],
    previous_hawkes: float,
    previous_latent: float,
    z_star: float,
    sigma_c: float,
    r_star: float,
) -> Dict[str, float]:
    weather = params.weather
    aggression, turnout = compute_weather_modulators(
        z_temp=weather.z_temp,
        z_pm25=weather.z_pm25,
        rain_mm=weather.rain_mm,
        daytime_temp_c=weather.daytime_temp_c,
        holiday=weather.holiday,
        delta_weekend=weather.delta_weekend,
    )
    if weather.aggression_override is not None:
        aggression = float(weather.aggression_override)
    if weather.turnout_override is not None:
        turnout = float(weather.turnout_override)

    events = params.events
    hawkes = update_hawkes_state(
        previous=previous_hawkes,
        decay=params.psychology.decay_hawkes,
        alpha=params.psychology.alpha_hawkes,
        dt=params.psychology.dt,
        active=events.active_protesters,
        population_pool=events.population_pool,
        agitator_weights=agitation_events,
    )
    latent = update_latent_discontent(
        previous=previous_latent,
        memory=params.psychology.rho_mem,
        gamma_econ=params.psychology.gamma_econ,
        rdi=rdi,
        aggression=aggression,
        lambda_cpt=2.25,
        delta_penalty=delta_penalty,
        compression=params.psychology.kappa_compress,
        turnout=turnout,
        beta_agit=params.psychology.beta_agit,
        agitator_weights=agitation_events,
        hawkes_prev=previous_hawkes,
        decay=params.psychology.decay_hawkes,
        dt=params.psychology.dt,
    )
    activation = (
        epstein_activation_rate(
            penalty=penalty,
            police_count=events.police_count,
            active_protesters=events.active_protesters,
            agents=params.psychology.epstein_agents,
            h_sigma=params.psychology.epstein_h_sigma,
            r_sigma=params.psychology.epstein_r_sigma,
        )
        if events.enable_epstein
        else 0.0
    )
    return {
        "aggression": aggression,
        "turnout": turnout,
        "hawkes": hawkes,
        "latent_discontent": latent,
        "epstein_activation_rate": activation,
        "granovetter_z_star": z_star,
        "granovetter_sigma_c": sigma_c,
        "granovetter_r_star": r_star,
    }


def granovetter_constants(mu_theta: float) -> Tuple[float, float, float]:
    return granovetter_fold(mu_theta)


__all__ = [
    "classify_agitated",
    "epstein_activation_rate",
    "esim_conversion",
    "event_layer",
    "event_severity",
    "event_virality",
    "granovetter_constants",
    "interest_group_magnitudes",
    "military_layer",
    "plover_goldstein",
    "power_layer",
    "psychology_layer",
    "resolve_network",
    "structural_layer",
]
