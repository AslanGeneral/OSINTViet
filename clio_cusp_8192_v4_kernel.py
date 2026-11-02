"""Numerical and feature-engineering primitives for CLIO-CUSP 8192 v4.0.

Heavy model packages are imported only by the adapters that need them. The
module does not download checkpoints, select a device, or initialize a GPU.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

R_REPLICATES = 9
M_PARTICLES = 8192
M_BITS = 13
H_HORIZON = 128
U_CLIP_EPS = 2.0 ** -14
AGE_YOUTH_BULGE = (20, 29)
NU_STAR_LIMIT = 2.0
LAMBDA_CPT = 2.25
ALPHA_CPT = 0.88
BETA_VULN = 0.95
K_EPSTEIN = 2.30
T_ACT_EPSTEIN = 0.10
SIGMA_C_GRAN = 0.1222
F_MIL_OPT = 2.0
BETA_TEMP = 0.113
BETA_PM25 = 0.029
GAMMA_RAIN = 0.25
T_NAE_CELSIUS = 35.0


def compute_structural_virality_o_n(parent_indices: Sequence[int]) -> float:
    """Mean pairwise tree distance via a single O(n) reverse traversal.

    Node zero is the root and every later node's parent must precede it.
    A one-node tree has structural virality zero.
    """
    n = len(parent_indices)
    if n <= 1:
        if n == 1 and parent_indices[0] != -1:
            raise ValueError("the root parent must be -1")
        return 0.0
    if parent_indices[0] != -1:
        raise ValueError("parent_indices[0] must be -1")
    for i, parent in enumerate(parent_indices[1:], 1):
        if not isinstance(parent, (int, np.integer)) or parent < 0 or parent >= i:
            raise ValueError(f"parent_indices[{i}] must be an integer in [0, {i})")
    subtree = [1] * n
    distance_sum = 0
    for i in range(n - 1, 0, -1):
        size = subtree[i]
        distance_sum += size * (n - size)
        subtree[parent_indices[i]] += size
    return 2.0 * distance_sum / (n * (n - 1))


def _spectral_radius(matrix: sp.csr_matrix) -> float:
    if matrix.nnz == 0:
        return 0.0
    n = matrix.shape[0]
    if n == 1:
        return abs(float(matrix[0, 0]))
    if n == 2:
        return float(np.max(np.abs(np.linalg.eigvals(matrix.toarray()))))
    try:
        value = spla.eigs(matrix.astype(np.float64), k=1, which="LM", return_eigenvectors=False)[0]
        return float(abs(value))
    except (spla.ArpackError, ValueError, RuntimeError, TypeError):
        # Frobenius norm is a conservative upper bound for the spectral radius.
        return float(spla.norm(matrix))


def _bilinear_katz(matrix: sp.csr_matrix, beta: float) -> np.ndarray:
    square = matrix @ matrix
    identity = sp.eye(matrix.shape[0], format="csr", dtype=np.float64)
    rhs = (matrix + beta * square) @ np.ones(matrix.shape[0], dtype=np.float64)
    return np.asarray(spla.spsolve(identity - beta * beta * square, rhs), dtype=np.float64)


def compute_signed_bilinear_katz_bonacich(
    A_pos: sp.spmatrix,
    A_neg: sp.spmatrix,
    omega_conflict: float = 0.5,
    eps_floor: float = 1e-6,
) -> np.ndarray:
    """Compute positive centrality from even-length paths in each layer."""
    pos = sp.csr_matrix(A_pos, dtype=np.float64)
    neg = sp.csr_matrix(A_neg, dtype=np.float64)
    if pos.shape != neg.shape or pos.shape[0] != pos.shape[1]:
        raise ValueError("A_pos and A_neg must be square matrices of equal shape")
    if omega_conflict < 0 or eps_floor <= 0:
        raise ValueError("omega_conflict must be nonnegative and eps_floor positive")
    if (pos.data < 0).any() or (neg.data < 0).any():
        raise ValueError("input layers must contain nonnegative edge weights")
    n = pos.shape[0]
    if n == 0:
        return np.empty(0, dtype=np.float64)
    beta = 0.85 / max(_spectral_radius(pos), _spectral_radius(neg), 1e-6)
    cp, cn = _bilinear_katz(pos, beta), _bilinear_katz(neg, beta)
    return np.sqrt(cp * cp + omega_conflict * cn * cn) + eps_floor


def xy_to_hilbert_13bit(x: Any, y: Any) -> Any:
    """Map 13-bit integer coordinates to standard 26-bit Hilbert distances.

    Accepts NumPy arrays or torch tensors and returns the same array family.
    The algorithm follows the iterative Hilbert rotation, preserving the
    unprocessed coordinate bits at every level.
    """
    is_torch = type(x).__module__.split(".")[0] == "torch"
    if is_torch:
        import torch
        xx, yy = x.clone().to(dtype=torch.int64), y.clone().to(dtype=torch.int64)
        if bool(((xx < 0) | (xx >= M_PARTICLES) | (yy < 0) | (yy >= M_PARTICLES)).any()):
            raise ValueError("Hilbert coordinates must be in [0, 8191]")
        d = torch.zeros_like(xx)
        for level in range(M_BITS - 1, -1, -1):
            s = 1 << level
            rx = (xx & s) != 0
            ry = (yy & s) != 0
            d += (s * s) * ((3 * rx.to(torch.int64)) ^ ry.to(torch.int64))
            rotate = ~ry
            flip = rotate & rx
            xx = torch.where(flip, (M_PARTICLES - 1) - xx, xx)
            yy = torch.where(flip, (M_PARTICLES - 1) - yy, yy)
            xx, yy = torch.where(rotate, yy, xx), torch.where(rotate, xx, yy)
        return d
    xx, yy = np.asarray(x, dtype=np.int64).copy(), np.asarray(y, dtype=np.int64).copy()
    if np.any((xx < 0) | (xx >= M_PARTICLES) | (yy < 0) | (yy >= M_PARTICLES)):
        raise ValueError("Hilbert coordinates must be in [0, 8191]")
    d = np.zeros(np.broadcast_shapes(xx.shape, yy.shape), dtype=np.int64)
    xx, yy = np.broadcast_arrays(xx, yy)
    for level in range(M_BITS - 1, -1, -1):
        s = 1 << level
        rx, ry = (xx & s) != 0, (yy & s) != 0
        d += (s * s) * ((3 * rx.astype(np.int64)) ^ ry.astype(np.int64))
        rotate = ~ry
        flip = rotate & rx
        xx, yy = np.where(flip, M_PARTICLES - 1 - xx, xx), np.where(flip, M_PARTICLES - 1 - yy, yy)
        xx, yy = np.where(rotate, yy, xx), np.where(rotate, xx, yy)
    return d


def compute_true_2d_hilbert_rank_permutation(X_t: Any, b_t: Any) -> Any:
    """Return each particle's Hilbert rank, from state and splitting-factor ranks."""
    if type(X_t).__module__.split(".")[0] == "torch":
        import torch
        if X_t.shape != b_t.shape or X_t.shape[-1] != M_PARTICLES:
            raise ValueError("X_t and b_t must have matching shape [..., 8192]")
        rx = torch.argsort(torch.argsort(X_t, dim=-1), dim=-1)
        rb = torch.argsort(torch.argsort(b_t, dim=-1), dim=-1)
        distance = xy_to_hilbert_13bit(rx, rb)
        order = torch.argsort(distance, dim=-1)
        ranks = torch.empty_like(order)
        rank_values = torch.arange(M_PARTICLES, device=order.device).expand_as(order)
        return ranks.scatter(-1, order, rank_values)
    xa, ba = np.asarray(X_t), np.asarray(b_t)
    if xa.shape != ba.shape or xa.shape[-1] != M_PARTICLES:
        raise ValueError("X_t and b_t must have matching shape [..., 8192]")
    rx = np.argsort(np.argsort(xa, axis=-1), axis=-1)
    rb = np.argsort(np.argsort(ba, axis=-1), axis=-1)
    order = np.argsort(xy_to_hilbert_13bit(rx, rb), axis=-1)
    return np.argsort(order, axis=-1)


def draw_owen_scrambled_0_13_2_nets(step_seed: int, device: Any = "cpu", dtype: Any = None) -> tuple[Any, Any]:
    """Draw nine independently scrambled two-dimensional Sobol point sets."""
    import torch
    if dtype is None:
        dtype = torch.float64
    coordinates = []
    for r in range(R_REPLICATES):
        engine = torch.quasirandom.SobolEngine(
            dimension=2, scramble=True, seed=(int(step_seed) * 1009 + r * 9176 + 17) % (2**31 - 1)
        )
        coordinates.append(engine.draw(M_PARTICLES, dtype=dtype).to(device))
    points = torch.stack(coordinates)
    return points[..., 0], points[..., 1]


def phi1_hochbruck(z: Any) -> Any:
    """Stable phi_1(z)=(exp(z)-1)/z, including its analytic value at zero."""
    import torch
    z = torch.as_tensor(z)
    small = torch.abs(z) < 1e-4
    taylor = 1 + z * (0.5 + z * (1 / 6 + z / 24))
    safe = torch.where(small, torch.ones_like(z), z)
    return torch.where(small, taylor, torch.expm1(safe) / safe)


def compute_cusp_barrier_and_crit(a_t: Any, b_t: Any) -> tuple[Any, Any]:
    import torch
    b_pos = torch.clamp(b_t, min=1e-7)
    a_crit = torch.sqrt((4 / 27) * b_pos**3)
    ratio = torch.clamp(a_t / a_crit, -1.0, 1.0)
    barrier = -2 * torch.sqrt(b_pos / 3) * torch.sin(torch.asin(ratio) / 3)
    return torch.where(b_t > 0, barrier, torch.zeros_like(a_t)), a_crit


def _ndtr(x: Any) -> Any:
    import torch
    return torch.special.ndtr(x)


def clio_cusp_v4_array_mrqmc_step(
    X_t: Any, a_t: Any, b_t: Any, Sigma_t: Any,
    v_sort_sobol: Any, u_step_sobol: Any,
    sigma_a: float = 0.02, sigma_b: float = 0.02, dt: float = 1.0,
) -> dict[str, Any]:
    """Advance the [9,8192] particle array and return smoothed estimates."""
    import torch
    tensors = (X_t, a_t, b_t, Sigma_t, v_sort_sobol, u_step_sobol)
    if any(t.shape != (R_REPLICATES, M_PARTICLES) for t in tensors):
        raise ValueError("all particle and Sobol tensors must have shape [9, 8192]")
    if dt <= 0 or sigma_a < 0 or sigma_b < 0:
        raise ValueError("dt must be positive and forecast standard deviations nonnegative")
    hilbert_rank = compute_true_2d_hilbert_rank_permutation(X_t, b_t)
    v_order = torch.argsort(v_sort_sobol, dim=-1)
    sorted_u = torch.gather(u_step_sobol, -1, v_order)
    u_particle = torch.gather(sorted_u, -1, hilbert_rank)
    u_safe = torch.clamp(u_particle, U_CLIP_EPS, 1 - U_CLIP_EPS)
    dW = torch.special.ndtri(u_safe) * math.sqrt(dt)

    z = -2 * b_t * dt
    x_tilde = X_t / torch.sqrt(torch.exp(z) + 2 * dt * phi1_hochbruck(z) * X_t.square())
    drift_target = x_tilde + a_t * dt
    diffusion_std = torch.clamp(Sigma_t * math.sqrt(dt), min=1e-7)
    barrier, a_crit = compute_cusp_barrier_and_crit(a_t, b_t)
    p_kramers = _ndtr((drift_target - barrier) / diffusion_std)
    p_fold = _ndtr((a_t - a_crit) / max(sigma_a, 1e-4)) * _ndtr(b_t / max(sigma_b, 1e-4))
    p_collapse = p_kramers + p_fold - p_kramers * p_fold
    sigma_delta = torch.sqrt((54 * a_t * sigma_a).square() + (12 * b_t.square() * sigma_b).square() + 1e-6)
    p_hysteresis = _ndtr((4 * b_t.pow(3) - 27 * a_t.square()) / sigma_delta) * _ndtr(b_t / max(sigma_b, 1e-4))
    X_next = drift_target + Sigma_t * dW
    S_next = torch.sigmoid(-X_next)
    collapse_replicate = p_collapse.mean(dim=-1)
    hysteresis_replicate = p_hysteresis.mean(dim=-1)
    stability_replicate = S_next.mean(dim=-1)
    return {
        "X_next": X_next,
        "S_next_mRQMC": torch.median(stability_replicate),
        "P_hysteresis_mRQMC": torch.median(hysteresis_replicate),
        "P_collapse_mRQMC": torch.median(collapse_replicate),
        "Volatility_mean": Sigma_t.mean(),
    }


def compute_mmp_aug(gdp_pc: float, median_wage: float, urban_share: float,
                    youth_share: float, top10_share: float, bottom50_share: float,
                    productivity_growth: float, median_wage_growth: float,
                    omega_gini: float = 1.0, omega_tech: float = 1.0) -> tuple[float, float]:
    if median_wage <= 0 or bottom50_share <= 0:
        raise ValueError("median_wage and bottom50_share must be positive")
    auto_ineq = omega_gini * top10_share / bottom50_share + omega_tech * max(0.0, productivity_growth - median_wage_growth)
    return (gdp_pc / median_wage) * urban_share * youth_share * (1 + auto_ineq), auto_ineq


def compute_emp(elite_share: float, labor_share: float, top1_share: float, top10_share: float) -> float:
    if not 0 <= elite_share <= 1 or not 0 <= labor_share < 1 or top10_share <= 0:
        raise ValueError("elite_share, labor_share, and top10_share are outside their valid domains")
    return elite_share**2 / (1 - labor_share) * (1 + top1_share / top10_share)


def compute_sfd(public_debt_gdp: float, interest_plus_deficit_tax: float,
                w_debt: float = 1.0, w_def: float = 1.0) -> float:
    return w_debt * public_debt_gdp + w_def * interest_plus_deficit_tax


def compute_rdi(expected_value: float, capability_value: float) -> float:
    if expected_value <= 0:
        raise ValueError("expected_value must be positive")
    return min(1.0, max(0.0, (expected_value - capability_value) / expected_value))


def update_psi(psi_prev: float, mmp_aug: float, emp: float, sfd: float,
               sclerosis: float, reform: float, delta_psi: float,
               eta_reform: float) -> float:
    value = (1 - delta_psi) * psi_prev + delta_psi * mmp_aug * emp * sfd
    return max(0.0, value - eta_reform * (1 - sclerosis) * reform)


def compute_power_metrics(shares: Sequence[float], selectorate: float, coalition: float) -> dict[str, float]:
    p = np.asarray(shares, dtype=np.float64)
    if p.ndim != 1 or len(p) == 0 or np.any(p < 0) or not np.isclose(p.sum(), 1.0):
        raise ValueError("shares must be a nonempty probability vector summing to one")
    if selectorate <= 0 or not 0 <= coalition <= selectorate:
        raise ValueError("require selectorate > 0 and coalition in [0, selectorate]")
    hhi = float(p @ p)
    return {"HHI_pol": hhi, "PCI": hhi * (1 - coalition / selectorate), "ENP": 1 / hhi}


def compute_sclerosis(lambda_olson: float, stability_duration: float, veto_players: float,
                      rent_gdp: float) -> float:
    return float(1 - math.exp(-lambda_olson * stability_duration * veto_players * rent_gdp))


def compute_military_fragmentation(shares: Sequence[float]) -> float:
    s = np.asarray(shares, dtype=np.float64)
    if s.ndim != 1 or s.size == 0 or np.any(s < 0) or not np.isclose(s.sum(), 1.0):
        raise ValueError("military shares must be nonnegative and sum to one")
    return float(1 / np.sum(s * s))


def compute_smr(active_military: float, paramilitary: float, reserve: float,
                population: float, military_expenditure: float, gdp: float,
                active_max: float, reserve_max: float, expenditure_max: float) -> float:
    if population <= 0 or gdp <= 0 or min(active_max, reserve_max, expenditure_max) <= 0:
        raise ValueError("population, GDP, and normalization maxima must be positive")
    active_term = math.log1p(max(0.0, active_military + paramilitary) / population * 1000) / math.log1p(active_max)
    reserve_term = math.log1p(max(0.0, reserve) / population * 1000) / math.log1p(reserve_max)
    expenditure_term = math.log1p(max(0.0, military_expenditure) / gdp * 100) / math.log1p(expenditure_max)
    return min(1.0, max(0.0, (active_term + reserve_term + expenditure_term) / 3))


def granovetter_fold(mu_theta: float = 0.25) -> tuple[float, float, float]:
    """Return (z*, sigma_c, r*) for the saddle-node fold of the threshold map."""
    from scipy.optimize import brentq
    from scipy.special import ndtr
    def pdf(z: float) -> float:
        return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    def equation(z: float) -> float:
        return float(ndtr(z)) - z * pdf(z) - mu_theta
    z_star = brentq(equation, -12.0, 12.0)
    return z_star, pdf(z_star), float(ndtr(z_star))


def granovetter_participation(previous_rate: float, mu_theta: float,
                              sigma_theta: float) -> float:
    from scipy.special import ndtr
    if sigma_theta <= 0:
        raise ValueError("sigma_theta must be positive")
    return float(ndtr((previous_rate - mu_theta) / sigma_theta))


def compute_cusp_parameters(psi: float, spi: float, omega_total: float,
                            regime_change_probability: float, foreign_pressure: float,
                            penalty: float, latent_discontent: float,
                            sclerosis: float, esim_conversion: float, asi: float,
                            weights_a: Mapping[str, float], weights_b: Mapping[str, float],
                            a0: float = 0.0) -> tuple[float, float]:
    a = (a0 + weights_a["psi"] * psi + weights_a["spi"] * spi**2
         + weights_a["ig"] * omega_total + weights_a["coup"] * regime_change_probability
         + vulnerability_amplifier(penalty) * foreign_pressure)
    b = (weights_b["latent"] * latent_discontent + weights_b["penalty"] * penalty
         + weights_b["sclerosis"] * sclerosis + weights_b["esim"] * esim_conversion
         - weights_b["asi"] * asi)
    return a, b


def diffusion_volatility(sigma0: float, penalty: float, aggression: float,
                         turnout: float, sigma_theta: float,
                         sigma_c: float = SIGMA_C_GRAN) -> float:
    if sigma_c <= 0:
        raise ValueError("sigma_c must be positive")
    return (sigma0 * vulnerability_amplifier(penalty) * aggression * turnout
            * (1 + sigma_theta / sigma_c))


def logistic(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1 + ex)


def coup_probabilities(z_attempt: float, asi_outcome: float, penalty: float,
                       elite_defection: float, alpha_def: float,
                       alpha_legit: float, alpha_defection: float) -> dict[str, float]:
    attempt = logistic(z_attempt)
    success = logistic(-alpha_def * asi_outcome + alpha_legit * penalty + alpha_defection * elite_defection)
    return {"attempt": attempt, "success_given_attempt": success,
            "regime_change": attempt * success, "bloody_failed": attempt * (1 - success)}


def compute_weather_modulators(z_temp: float, z_pm25: float, rain_mm: float,
                               daytime_temp_c: float, holiday: bool = False,
                               delta_weekend: float = 0.0) -> tuple[float, float]:
    aggression = (1 + BETA_TEMP * max(0.0, z_temp)) * (1 + BETA_PM25 * max(0.0, z_pm25))
    turnout = math.exp(-GAMMA_RAIN * max(0.0, rain_mm)) * math.exp(-0.04 * max(0.0, daytime_temp_c - T_NAE_CELSIUS) ** 2)
    if holiday:
        turnout *= 1 + delta_weekend
    return aggression, turnout


def update_hawkes_state(previous: float, decay: float, alpha: float, dt: float,
                        active: float, population_pool: float, agitator_weights: Iterable[float]) -> float:
    if population_pool <= 0 or dt < 0:
        raise ValueError("population_pool must be positive and dt nonnegative")
    depletion = max(0.0, 1 - active / population_pool)
    return math.exp(-decay * dt) * previous + alpha * depletion * sum(agitator_weights)


def update_latent_discontent(previous: float, memory: float, gamma_econ: float, rdi: float,
                             aggression: float, lambda_cpt: float, delta_penalty: float,
                             compression: float, turnout: float, beta_agit: float,
                             agitator_weights: Iterable[float], hawkes_prev: float,
                             decay: float, dt: float) -> float:
    agit = sum(agitator_weights)
    return (memory * previous + gamma_econ * rdi
            + aggression * lambda_cpt * delta_penalty * (1 + compression * (1 - turnout))
            + beta_agit * (agit + hawkes_prev * math.exp(-decay * dt)))


def penalty_increment(impact: float) -> float:
    """Map impact in (0,2] to the specification's negative-exponent ladder."""
    if not 0 < impact <= 2:
        raise ValueError("impact must be in (0, 2]")
    bounds = ((0.0, 0.15, 4), (0.15, 0.50, 3), (0.50, 1.20, 2), (1.20, 2.0, 1))
    for lower, upper, k in bounds:
        if impact <= upper:
            mantissa = 1 + 8.9 * (impact - lower) / (upper - lower)
            break
    return mantissa * 10.0**(-k)


def vulnerability_amplifier(penalty: float, beta_vuln: float = BETA_VULN) -> float:
    p = min(1.0, max(0.0, penalty))
    return 1 / (1 - beta_vuln * p)


def update_penalty_state(previous: float, positive_increments: Iterable[float],
                         remediation: Iterable[tuple[float, float]], sclerosis: float,
                         delay_rate: float, accumulated_bias: float = 0.0,
                         lambda_cpt: float = LAMBDA_CPT) -> tuple[float, float]:
    """Apply event penalties and delayed remediation; return (P_t, B_t).

    Remediation entries are (amount, age_days). Delayed penalties are expected
    to be added by the caller when their event age becomes known.
    """
    if lambda_cpt <= 0 or delay_rate < 0:
        raise ValueError("lambda_cpt must be positive and delay_rate nonnegative")
    bias = min(1.0, max(0.0, accumulated_bias))
    additions = sum(max(0.0, value) for value in positive_increments)
    recovery = sum(math.exp(-delay_rate * max(0.0, age)) * max(0.0, amount)
                   for amount, age in remediation)
    value = previous + additions - ((1 - min(1.0, max(0.0, sclerosis))) / lambda_cpt) * recovery
    return min(1.0, max(bias, value)), bias


def update_accumulated_bias(previous: float, delayed_increments: Iterable[tuple[float, float]],
                            theta_bias: float, delay_rate: float) -> float:
    """Update the lower floor B_t from (penalty increment, delay days) pairs."""
    if delay_rate < 0 or theta_bias < 0:
        raise ValueError("delay_rate and theta_bias must be nonnegative")
    value = previous + sum(theta_bias * (1 - math.exp(-delay_rate * max(0.0, age)))
                           * max(0.0, increment)
                           for increment, age in delayed_increments)
    return min(1.0, max(0.0, value))


def compute_coup_attempt_logit(intercept: float, penalty: float, emp: float,
                               gdp_pc_log_growth: float, fragmentation_change_365: float,
                               harbingers: Sequence[float], beta_penalty: float,
                               beta_emp: float, beta_gdp: float,
                               beta_preempt: float, gamma: Sequence[float]) -> float:
    if len(harbingers) != 4 or len(gamma) != 4:
        raise ValueError("exactly four harbinger values and coefficients are required")
    return (intercept + beta_penalty * penalty + beta_emp * emp
            - beta_gdp * gdp_pc_log_growth
            + beta_preempt * max(0.0, fragmentation_change_365)
            + sum(float(c) * float(x) for c, x in zip(gamma, harbingers)))


def compute_regime_defense_index(military_expenditure: float, military_total: float,
                                 baseline_pay: float, fragmentation: float,
                                 selectorate: float, coalition: float,
                                 eta_coup: float) -> float:
    if military_total <= 0 or baseline_pay <= 0 or selectorate <= 0:
        raise ValueError("military_total, baseline_pay, and selectorate must be positive")
    if not 0 <= coalition <= selectorate:
        raise ValueError("coalition must be in [0, selectorate]")
    return (math.exp(-eta_coup * (fragmentation - F_MIL_OPT) ** 2)
            * math.tanh((military_expenditure / military_total) / baseline_pay)
            * (1 - coalition / selectorate))


def compute_severity_score(goldstein_expectation: float, size_weight: float,
                           damage: float, baseline_loss: float) -> float:
    """Event severity in [0, 1]; the damage term is capped at the locked ceiling.

    A zero-impact event yields 0.0 and should be filtered before the penalty
    ladder, whose domain is (0, 2].
    """
    if baseline_loss <= 0 or damage < 0 or size_weight < 0:
        raise ValueError("baseline_loss must be positive; damage and size_weight nonnegative")
    score = (abs(goldstein_expectation) / 10) * (size_weight / 5) * (1 + math.log1p(damage / baseline_loss))
    return min(1.0, max(0.0, score))


def compute_social_amplification(negative_event_score: float, new_shares_per_day: float,
                                 virality: float, alpha_social: float) -> float:
    if not 0 <= negative_event_score <= 1 or new_shares_per_day < 0 or virality < 0:
        raise ValueError("event score and virality inputs must be nonnegative and in range")
    return negative_event_score * math.tanh(alpha_social * math.log1p(new_shares_per_day) * max(1.0, virality / NU_STAR_LIMIT))


def compute_asi_outcome(fragmentation: float, military_expenditure: float,
                        military_total: float, baseline_pay: float,
                        selectorate: float, coalition: float, eta_coup: float) -> float:
    return compute_regime_defense_index(military_expenditure, military_total, baseline_pay,
                                        fragmentation, selectorate, coalition, eta_coup)


def crowd_size_weight(headcount: float | None) -> float:
    if headcount is None or headcount <= 0:
        return 1.0
    if headcount < 20:
        return 1.0
    if headcount < 100:
        return 2.0
    if headcount < 1_000:
        return 3.0
    if headcount < 10_000:
        return 4.0
    return 1 + math.log10(headcount)


def create_event_frame(records: Sequence[Mapping[str, Any]]) -> Any:
    """Create a normalized in-memory Polars event table without network IO."""
    import polars as pl
    fields = ("event_id", "event_time", "source", "target", "event_type", "mode", "context",
              "probabilities", "headcount", "damage", "parent_indices")
    normalized = [{key: record.get(key) for key in fields} for record in records]
    if not normalized:
        return pl.DataFrame({field: [] for field in fields})
    return pl.DataFrame(normalized, infer_schema_length=None)


class EventIngestionService:
    """Wire externally supplied GENOME deduplication and PLOVER classification.

    GENOME/NGEC classifier heads are not embedded in the base encoder weights;
    callers inject the trained deduplicator and classifier so their versions,
    label maps, and provenance remain explicit.
    """
    def __init__(self, deduplicator: Any, classifier: Any):
        if not callable(deduplicator) or not callable(classifier):
            raise TypeError("deduplicator and classifier must be callable")
        self.deduplicator = deduplicator
        self.classifier = classifier

    def process(self, records: Sequence[Mapping[str, Any]]) -> Any:
        unique = self.deduplicator(records)
        enriched = []
        for record in unique:
            result = dict(record)
            classified = self.classifier(result)
            if not isinstance(classified, Mapping):
                raise TypeError("classifier must return a mapping of event labels and probabilities")
            result.update(classified)
            enriched.append(result)
        return create_event_frame(enriched)


def load_conflict_encoder(model_id: str = "eventdata-utd/ConfliBERT-2-base", **kwargs: Any) -> Any:
    """Lazily load the Hugging Face tokenizer/model; caller manages device placement."""
    from transformers import AutoModel, AutoTokenizer
    return AutoTokenizer.from_pretrained(model_id, **kwargs), AutoModel.from_pretrained(model_id, **kwargs)


def load_timesfm(checkpoint: str = "google/timesfm-2.5-200m-pytorch", *,
                 max_context: int = 1024, horizon: int = H_HORIZON,
                 device: str = "cpu", per_core_batch_size: int = 16,
                 allow_noncommercial_checkpoint: bool = False,
                 **kwargs: Any) -> Any:
    """Load a TimesFM forecaster lazily.

    TimesFM 2.5 is the self-hosted default. TimesFM 3.0's released weights
    require a noncommercial-use acknowledgement for local inference.
    """
    if "timesfm-3.0" in checkpoint:
        if not allow_noncommercial_checkpoint:
            raise ValueError("Self-hosted TimesFM 3.0 weights require explicit noncommercial-use acknowledgement")
        try:
            from timesfm3 import ModelConfig, TimesFM3Evaluator
        except ImportError as exc:
            raise RuntimeError("Install the TimesFM 3.0 package exposing timesfm3") from exc
        config = ModelConfig(checkpoint_path=checkpoint, per_core_batch_size=per_core_batch_size,
                             device=device, **kwargs)
        return TimesFM3Evaluator(config)
    try:
        import timesfm
    except ImportError as exc:
        raise RuntimeError("Install the CLIO-CUSP project dependencies to use TimesFM") from exc
    model_cls = getattr(timesfm, "TimesFM_2p5_200M_torch", None)
    config_cls = getattr(timesfm, "ForecastConfig", None)
    if model_cls is None or config_cls is None:
        raise RuntimeError("TimesFM 2.5 PyTorch API was not found; expected TimesFM_2p5_200M_torch and ForecastConfig")
    model = model_cls.from_pretrained(checkpoint, **kwargs)
    model.compile(config_cls(max_context=max_context, max_horizon=max(horizon, 128),
                             normalize_inputs=True, per_core_batch_size=per_core_batch_size,
                             use_continuous_quantile_head=True, fix_quantile_crossing=True,
                             return_backcast=True))
    return model


def _as_series_batch(history: Any) -> list[Any]:
    """Normalize TimesFM 2.5 inputs to a list of one-dimensional series."""
    if isinstance(history, (list, tuple)):
        return list(history)
    array = np.asarray(history)
    if array.ndim == 1:
        return [array]
    if array.ndim == 2:
        return [row for row in array]
    return [array]


def forecast_timesfm(model: Any, history: Any, horizon: int = H_HORIZON,
                     dynamic_numeric_covariates: Mapping[str, Any] | None = None,
                     dynamic_categorical_covariates: Mapping[str, Any] | None = None,
                     static_numeric_covariates: Mapping[str, Any] | None = None,
                     static_categorical_covariates: Mapping[str, Any] | None = None) -> Any:
    """Forecast one or more series; covariates are passed through to XReg."""
    if not 1 <= horizon <= H_HORIZON:
        raise ValueError(f"horizon must be between 1 and {H_HORIZON}")
    # TimesFM 3's evaluator exposes predict/predict_batch and accepts NumPy
    # arrays; multivariate input has shape [variates, context_length].
    if hasattr(model, "predict"):
        kwargs: dict[str, Any] = {"horizon": horizon, "return_quantiles": True}
        if dynamic_numeric_covariates is not None:
            kwargs["past_future_covariates"] = dynamic_numeric_covariates
        return model.predict(history, **kwargs)
    if hasattr(model, "predict_batch"):
        kwargs = {"horizon": horizon, "return_quantiles": True}
        if dynamic_numeric_covariates is not None:
            kwargs["past_future_covariates"] = dynamic_numeric_covariates
        return list(model.predict_batch([history], **kwargs))[0]
    if hasattr(model, "forecast_with_covariates") and any((dynamic_numeric_covariates,
                                                            dynamic_categorical_covariates,
                                                            static_numeric_covariates,
                                                            static_categorical_covariates)):
        return model.forecast_with_covariates(
            inputs=_as_series_batch(history),
            dynamic_numerical_covariates=dynamic_numeric_covariates or {},
            dynamic_categorical_covariates=dynamic_categorical_covariates or {},
            static_numerical_covariates=static_numeric_covariates or {},
            static_categorical_covariates=static_categorical_covariates or {},
            xreg_mode="timesfm + xreg",
        )
    if hasattr(model, "forecast"):
        return model.forecast(horizon=horizon, inputs=_as_series_batch(history))
    raise TypeError("model does not expose a supported forecast method")


def build_signed_leiden_partitions(A_pos: sp.spmatrix, A_neg: sp.spmatrix,
                                   resolution: float = 0.1, seed: int = 0) -> list[list[int]]:
    """Optimize a signed CPM multiplex partition using python-igraph/leidenalg.

    The specification defines e_c as one half of the double sum over the
    symmetric adjacency matrix.  Graph libraries count every undirected edge
    once, so only the upper triangle of each symmetric layer is passed to
    Leiden; otherwise a matrix containing both (i, j) and (j, i) would
    double-count every edge and silently halve the effective resolution.
    """
    import igraph as ig
    import leidenalg
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    pos, neg = sp.csr_matrix(A_pos, dtype=np.float64), sp.csr_matrix(A_neg, dtype=np.float64)
    if pos.shape != neg.shape or pos.shape[0] != pos.shape[1]:
        raise ValueError("signed adjacency matrices must have equal square shapes")
    if (pos.data < 0).any() or (neg.data < 0).any():
        raise ValueError("signed adjacency layers must contain nonnegative edge weights")
    graphs = []
    for matrix in (pos, neg):
        unique = sp.triu(matrix, k=1).tocoo()
        if unique.nnz == 0 and sp.tril(matrix, k=-1).nnz:
            unique = sp.triu(matrix.T, k=1).tocoo()
        edges = [(int(i), int(j)) for i, j, w in zip(unique.row, unique.col, unique.data) if w > 0]
        weights = [float(w) for i, j, w in zip(unique.row, unique.col, unique.data) if w > 0]
        graph = ig.Graph(n=matrix.shape[0], edges=edges, directed=False)
        graph.es["weight"] = weights
        graphs.append(graph)
    parts = [leidenalg.CPMVertexPartition(g, weights="weight", resolution_parameter=resolution) for g in graphs]
    optimiser = leidenalg.Optimiser()
    optimiser.set_rng_seed(seed)
    optimiser.optimise_partition_multiplex(parts, layer_weights=[1.0, -1.0])
    memberships = parts[0].membership
    clusters: dict[int, list[int]] = {}
    for vertex, cluster in enumerate(memberships):
        clusters.setdefault(cluster, []).append(vertex)
    return list(clusters.values())


def build_sobol_state(R: int = R_REPLICATES, M: int = M_PARTICLES,
                      device: Any = "cpu", seed: int = 0) -> tuple[Any, Any, Any, Any]:
    """Allocate initial particle arrays and independent Sobol points for one step."""
    import torch
    if R != R_REPLICATES or M != M_PARTICLES:
        raise ValueError("production dimensions are locked to R=9 and M=8192")
    seeds = torch.randint(0, 2**31 - 1, (R,), generator=torch.Generator().manual_seed(seed)).tolist()
    x = torch.zeros((R, M), dtype=torch.float64, device=device)
    v_rows, u_rows = [], []
    for sub_seed in seeds:
        points = torch.quasirandom.SobolEngine(2, scramble=True, seed=sub_seed).draw(M, dtype=torch.float64).to(device)
        v_rows.append(points[:, 0]); u_rows.append(points[:, 1])
    return x, torch.stack(v_rows), torch.stack(u_rows), torch.tensor(seeds)

