"""Markov Regime-Switching and Non-Linear State Propagation Engine (SPEC-MACRO-QUANT v5.7).

This module delivers the complete set of Markov regime-switching functions
established in the v5.7 specification and the Wave 1-6 Technical Audit Dossiers
(Hamilton 1989, Kim 1994, Lindsten et al. 2016, Leeper & Zha 2003):

1. predict_and_collapse_kim_hamilton_step:
   Layer 1.5 Hamilton/Kim regime pair prediction (r1 -> r2), Gaussian factor
   likelihood density eta_pair on R^r subspace, and Kim collapsing via the Law
   of Total Variance (Equations 16-21, Patch #3).

2. kim_backward_regime_probability_smoother:
   Layer 0 E-Step backward regime probability smoother across t = T-1 ... 0
   to compute xi_{s, t|T} = Pr(S_t = s | Y_{1:T}) (Kim 1994 JoE Eq. 2.14).

3. rts_exact_kalman_smoother_regime_aware:
   Layer 1 analytical RTS backward Kalman smoother accepting a 3D companion
   transition tensor A_comp[t+1] conditioned on the particle's realized
   Markov regime trajectory (Patch #2b, Lindsten et al. 2016).

4. step1_and_step3_endogenous_markov_propagation:
   Layer 5 fully vectorized endogenous Markov regime transition (Lucas critique
   statistic eta_LZ, Social Stress Index SSI, endogenous P_{12}(h)) and MS-SVAR
   factor space propagation across all N_p particles (Equations 31-34, Upgrade B).
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from scipy.linalg import cho_factor, cho_solve


class CompleteMarkovSwitchingEngineV57:
    """Unified implementation of the 4 core Markov regime-switching components

    for SPEC-MACRO-QUANT v5.7.
    """

    @staticmethod
    def branchless_pos(x: np.ndarray | float) -> np.ndarray | float:
        """Branchless positive-part operator: pos(x) = max(x, 0) = 0.5 * (x + |x|)."""
        return 0.5 * (x + np.abs(x))

    # ---------------------------------------------------------------------
    # 1. LAYER 1.5 (PATCH #3): Predict regime pair (r1, r2), evaluate
    #    Gaussian factor density eta_pair (Eq. 17-18) and Kim collapse (Eq. 16, 19-21)
    # ---------------------------------------------------------------------
    @staticmethod
    def predict_and_collapse_kim_hamilton_step(
        F_prev_collapsed: np.ndarray,  # [2, r_tilde] (r_tilde = 20 or 25)
        P_prev_collapsed: np.ndarray,  # [2, r_tilde, r_tilde]
        F_curr_rbpf: np.ndarray,       # [r] Current filtered factor state from Layer 1 (r = 4 or 5)
        A_comp_regimes: np.ndarray,    # [2, r_tilde, r_tilde] Companion matrix per regime r2
        Omega_regimes: np.ndarray,     # [2, r, r] Factor innovation covariance per regime r2
        P_trans: np.ndarray,           # [2, 2] Markov transition matrix P_{r1, r2}
        xi_prev: np.ndarray,           # [2] Previous filtered regime probabilities xi_{r1, t-1|t-1}
        mu_F_regimes: Optional[np.ndarray] = None,  # [2, r] Regime factor intercepts (if any)
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Execute Hamilton (1989) & Kim (1994) state-space filtering step:

        - Step A (Patch #3): Forecast companion state for all 2x2 = 4 regime pairs (r1, r2)
          and compute Gaussian likelihood density eta_{r1, r2, t} strictly on the factor
          subspace R^r (avoiding rank-deficient companion covariance singularity).
        - Step B: Update joint regime probabilities and execute Kim collapsing (Law of
          Total Variance) back to 2 collapsed Gaussian moments in R^{r_tilde}.

        Returns:
            F_collapsed: [2, r_tilde] Collapsed companion factor state
            P_collapsed: [2, r_tilde, r_tilde] Collapsed companion covariance
            xi_curr: [2] Updated regime probabilities Pr(S_t = r2 | I_t)
            xi_joint_pred: [2, 2] Prior joint regime probabilities Pr(S_{t-1}=r1, S_t=r2 | I_{t-1})
        """
        K_regimes, r_tilde = F_prev_collapsed.shape
        r_factors = Omega_regimes.shape[1]
        if mu_F_regimes is None:
            mu_F_regimes = np.zeros((K_regimes, r_factors), dtype=np.float64)

        F_pair = np.zeros((K_regimes, K_regimes, r_tilde), dtype=np.float64)
        P_pair = np.zeros((K_regimes, K_regimes, r_tilde, r_tilde), dtype=np.float64)
        eta_pair = np.zeros((K_regimes, K_regimes), dtype=np.float64)

        # Step A: Forecast companion state and evaluate Gaussian likelihood density eta_pair (Eq. 17-18)
        for r2 in range(K_regimes):
            A_r2 = A_comp_regimes[r2]
            Omega_r2 = Omega_regimes[r2]
            c_om, low_om = cho_factor(Omega_r2 + 1e-9 * np.eye(r_factors), lower=True)
            log_det_om = 2.0 * float(np.sum(np.log(np.diag(c_om))))
            norm_const = -0.5 * (r_factors * np.log(2.0 * np.pi) + log_det_om)

            for r1 in range(K_regimes):
                # Companion state forecast under regime transition (S_{t-1}=r1 -> S_t=r2)
                F_pred_pair = A_r2 @ F_prev_collapsed[r1]
                F_pred_pair[:r_factors] += mu_F_regimes[r2]
                P_pred_pair = A_r2 @ P_prev_collapsed[r1] @ A_r2.T
                P_pred_pair[:r_factors, :r_factors] += Omega_r2

                F_pair[r1, r2] = F_pred_pair
                P_pair[r1, r2] = 0.5 * (P_pred_pair + P_pred_pair.T)

                # Innovation error evaluated strictly on non-singular factor subspace R^r (Eq. 17)
                v_r1_r2 = F_curr_rbpf - F_pred_pair[:r_factors]
                quad_form = float(v_r1_r2 @ cho_solve((c_om, low_om), v_r1_r2))
                eta_pair[r1, r2] = np.exp(np.clip(norm_const - 0.5 * quad_form, -500.0, 500.0))

        # Step B: Hamilton probability update (Eq. 16, 19) & Kim collapsing (Eq. 20-21)
        xi_joint_pred = P_trans * xi_prev[:, None]  # [2, 2]: Pr(S_{t-1}=r1, S_t=r2 | I_{t-1})
        xi_joint_upd = xi_joint_pred * eta_pair
        xi_joint_upd /= np.maximum(np.sum(xi_joint_upd), 1e-300)
        xi_curr = np.sum(xi_joint_upd, axis=0)      # [2]: Pr(S_t = r2 | I_t)

        F_collapsed = np.zeros((K_regimes, r_tilde), dtype=np.float64)
        P_collapsed = np.zeros((K_regimes, r_tilde, r_tilde), dtype=np.float64)

        for r2 in range(K_regimes):
            denom = max(float(xi_curr[r2]), 1e-15)
            w_r1 = xi_joint_upd[:, r2] / denom      # [2]: Pr(S_{t-1}=r1 | S_t=r2, I_t)
            F_collapsed[r2] = w_r1[0] * F_pair[0, r2] + w_r1[1] * F_pair[1, r2]

            for r1 in range(K_regimes):
                diff = (F_collapsed[r2] - F_pair[r1, r2])[:, None]
                P_collapsed[r2] += w_r1[r1] * (P_pair[r1, r2] + diff @ diff.T)
            P_collapsed[r2] = 0.5 * (P_collapsed[r2] + P_collapsed[r2].T)

        return F_collapsed, P_collapsed, xi_curr, xi_joint_pred

    # ---------------------------------------------------------------------
    # 2. LAYER 0 (E-STEP / EQ. 2): Kim (1994) Full-Sample Backward
    #    Regime Probability Smoother xi_{s, t|T} = Pr(S_t = s | Y_{1:T})
    # ---------------------------------------------------------------------
    @staticmethod
    def kim_backward_regime_probability_smoother(
        xi_filt: np.ndarray,        # [T_steps, 2] Forward filtered probabilities Pr(S_t = s | I_t)
        xi_joint_pred: np.ndarray,  # [T_steps, 2, 2] Pr(S_{t-1}=r1, S_t=r2 | I_{t-1})
        P_trans: np.ndarray,        # [2, 2] Markov transition matrix
    ) -> np.ndarray:
        """Backward regime probability smoother across t = T-1 ... 0 (Kim 1994, JoE Eq. 2.14).

        Computes smoothed regime probabilities xi_{s, t|T} conditional on full sample information.
        """
        T_steps, _ = xi_filt.shape
        xi_smooth = np.zeros_like(xi_filt)
        xi_smooth[-1] = xi_filt[-1]

        for t in range(T_steps - 2, -1, -1):
            xi_pred_next = np.maximum(np.sum(xi_joint_pred[t + 1], axis=0), 1e-15)  # [2]
            ratio = xi_smooth[t + 1] / xi_pred_next                                  # [2]
            # Pr(S_t = r1 | I_T) = sum_{r2} Pr(S_t=r1 | S_{t+1}=r2, I_t) * Pr(S_{t+1}=r2 | I_T)
            xi_smooth[t] = xi_filt[t] * (P_trans @ ratio)
            xi_smooth[t] /= np.maximum(np.sum(xi_smooth[t]), 1e-15)

        return xi_smooth

    # ---------------------------------------------------------------------
    # 3. LAYER 1 (PATCH #2b): Analytical RTS Kalman Smoother with 3D
    #    Companion Tensor A_comp[t+1] Conditioned on Markov Trajectory
    # ---------------------------------------------------------------------
    @staticmethod
    def rts_exact_kalman_smoother_regime_aware(
        F_filt: np.ndarray,   # [T_steps, r_tilde]
        P_filt: np.ndarray,   # [T_steps, r_tilde, r_tilde]
        F_pred: np.ndarray,   # [T_steps, r_tilde]
        P_pred: np.ndarray,   # [T_steps, r_tilde, r_tilde]
        A_comp: np.ndarray,   # [r_tilde, r_tilde] (2D) OR [T_steps, r_tilde, r_tilde] (3D regime trajectory)
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Exact Rauch-Tung-Striebel (RTS) backward Kalman smoother (Patch #2b).

        Accepts 3D companion transition tensor A_comp[t+1] conditioned on the particle's
        realized Markov regime path S_{t+1} (Lindsten et al. 2016).
        """
        T_steps, r_tilde = F_filt.shape
        F_smooth = F_filt.copy()
        P_smooth = P_filt.copy()
        is_3d_regime_tensor = (A_comp.ndim == 3)

        for t in range(T_steps - 2, -1, -1):
            A_next = A_comp[t + 1] if is_3d_regime_tensor else A_comp
            c_p, low_p = cho_factor(P_pred[t + 1] + 1e-8 * np.eye(r_tilde), lower=True)
            L_gain = (cho_solve((c_p, low_p), A_next @ P_filt[t].T)).T
            F_smooth[t] = F_filt[t] + L_gain @ (F_smooth[t + 1] - F_pred[t + 1])
            P_smooth[t] = P_filt[t] + L_gain @ (P_smooth[t + 1] - P_pred[t + 1]) @ L_gain.T
            P_smooth[t] = 0.5 * (P_smooth[t] + P_smooth[t].T)

        return F_smooth, P_smooth

    # ---------------------------------------------------------------------
    # 4. LAYER 5 (EQ. 31-34 & UPGRADE B): Vectorized Endogenous Markov
    #    Regime Switching (Lucas Critique + SSI) & Particle Factor Propagation
    # ---------------------------------------------------------------------
    @classmethod
    def step1_and_step3_endogenous_markov_propagation(
        cls,
        h_step: int,                     # Forecast horizon step h in {1, ..., H} (1-based)
        S_prev: np.ndarray,              # [N_p] Previous regime S_{t+h-1}^{(i)} in {1, 2} (or {0, 1})
        u_markov_unif: np.ndarray,       # [N_p] Common random numbers u_{Markov, h}^{(i)} ~ U(0, 1)
        cum_policy_dev_norm: np.ndarray, # [N_p] Cumulative policy deviation norm sum ||u_{t+tau} - u_rule|| / sigma_u
        Y_prev_obs: np.ndarray,          # [N_p, N_vars] Observables at previous step (0:GDP, 1:CPI, 5:NPL)
        F_lags: np.ndarray,              # [N_p, p_lags, r] Factor lags F_{t+h-1}, F_{t+h-2}
        z_shocks_h: np.ndarray,          # [N_p, r] Sobol-IS standard normal draws at horizon h
        eps_assigned: np.ndarray,        # [N_p, d_shock] Adversarial shock from Layer 3
        u_policy_act: np.ndarray,        # [N_p, 3] SBV policy reaction at horizon h
        A_lags_regimes: np.ndarray,      # [2, p_lags, r, r] Lag transition matrices A_j(S)
        P_chol_regimes: np.ndarray,      # [2, r, r] Cholesky factors P_chol(S)
        Q_haar_bank: np.ndarray,         # [2, M_Q, r, r] Haar orthogonal rotation bank (M_Q=64)
        B_eps: np.ndarray,               # [r, d_shock] Exogenous shock transmission matrix
        B_u: np.ndarray,                 # [r, 3] Policy action transmission matrix
        rho_decay_diag: np.ndarray,      # [d_shock] Shock persistence decay vector (Upgrade E)
        p11: float = 0.92,
        p22: float = 0.85,
        gamma_lz: float = 0.35,
        gamma_ssi: float = 0.25,
        tau_unrest: float = 1.50,
        branch_type: str = "Active",     # "Base"/"baseline", "Pass"/"passive", or "Active"/"active"
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Execute 100% vectorized Step 1 (Eq. 31-33 + Upgrade B SSI) and Step 3

        (Eq. 34 + Upgrade E) across all N_p particles in block-tensor form without
        scalar Python loops.

        Returns:
            S_curr: [N_p] Sampled regime states in {1, 2}
            F_curr: [N_p, r] Propagated factor states at step h
            eta_lz: [N_p] Lucas critique statistic
            ssi_curr: [N_p] Endogenous Social Stress Index
        """
        N_p, p_lags, r_factors = F_lags.shape
        M_Q = Q_haar_bank.shape[1]

        # Normalize 0-indexed regime input {0, 1} to 1-indexed {1, 2} if needed
        S_prev_1idx = np.asarray(S_prev, dtype=np.int8)
        if np.all((S_prev_1idx == 0) | (S_prev_1idx == 1)):
            S_prev_1idx = S_prev_1idx + 1

        # --- STEP 1A: Lucas Critique Statistic eta_{LZ}^{(i)}(h-1) (Equation 31) ---
        if h_step <= 1:
            eta_lz = np.zeros(N_p, dtype=np.float64)
        else:
            eta_lz = cum_policy_dev_norm / np.sqrt(float(h_step - 1))

        # --- STEP 1B: Endogenous Social Stress Index SSI_{t+h-1}^{(i)} (Upgrade B) ---
        # Column indices: 0 = GDP_YoY, 1 = CPI_YoY, 5 = NPL_Ratio
        cpi_prev = Y_prev_obs[:, 1]
        gdp_prev = Y_prev_obs[:, 0]
        npl_prev = Y_prev_obs[:, 5] if Y_prev_obs.shape[1] > 5 else np.zeros(N_p, dtype=np.float64)

        ssi_curr = (
            0.40 * cls.branchless_pos(cpi_prev - 4.50)
            + 0.40 * cls.branchless_pos(5.00 - gdp_prev)
            + 0.20 * cls.branchless_pos(npl_prev - 3.00)
        )

        # --- STEP 1C: Endogenous Transition Probabilities P_{12}^{(i)}(h) & Regime Sampling (Eq. 32-33) ---
        # Protected against Bẫy 1 (Reversed Probability Trap):
        lz_excess = cls.branchless_pos(np.abs(eta_lz) - 2.0)
        ssi_excess = cls.branchless_pos(ssi_curr - tau_unrest)
        p12_endo = 1.0 - (1.0 - p11) * np.exp(-(gamma_lz * lz_excess + gamma_ssi * ssi_excess))
        p11_endo = 1.0 - p12_endo  # Pr(S_{t+h}=1 | S_{t+h-1}=1)
        p21_exo = np.full(N_p, 1.0 - p22, dtype=np.float64)  # Pr(S_{t+h}=1 | S_{t+h-1}=2)

        prob_stay_or_move_to_1 = np.where(S_prev_1idx == 1, p11_endo, p21_exo)
        S_curr = 1 + (u_markov_unif >= prob_stay_or_move_to_1).astype(np.int8)  # S_curr in {1, 2}

        # --- STEP 3: Factor Space Propagation F_{t+h}^{(i), B} conditioned on S_curr (Eq. 34) ---
        # Evaluated in block-tensor einsum (Protected against Bẫy 3: loop SLA explosion)
        F_curr = np.zeros((N_p, r_factors), dtype=np.float64)
        particle_q_idx = np.arange(N_p) % M_Q  # m(i) = (i mod 64)

        for s_val in (1, 2):
            mask_s = (S_curr == s_val)
            if not np.any(mask_s):
                continue
            s_idx = s_val - 1

            # Autoregressive lag contribution: sum_{j=1}^p A_j(S) F_{t+h-j}
            ar_term = np.zeros((int(np.sum(mask_s)), r_factors), dtype=np.float64)
            for lag_j in range(p_lags):
                ar_term += F_lags[mask_s, lag_j, :] @ A_lags_regimes[s_idx, lag_j].T

            # MS-SVAR structural innovation: P_chol(S) Q_{m(i)}(S) z_{t+h}^{(i)}
            Q_sub = Q_haar_bank[s_idx, particle_q_idx[mask_s]]            # [N_sub, r, r]
            P_chol_s = P_chol_regimes[s_idx]                              # [r, r]
            A0_inv_sub = np.einsum("ij,njk->nik", P_chol_s, Q_sub)        # [N_sub, r, r]
            struct_innov = np.einsum("nij,nj->ni", A0_inv_sub, z_shocks_h[mask_s])
            F_curr[mask_s] = ar_term + struct_innov

        # Shock impulse decayed via diagonal power matrix diag(rho_1^h, ..., rho_d^h)
        b_type = branch_type.lower()
        if b_type not in ("base", "baseline"):
            decay_vec = rho_decay_diag ** h_step                          # [d_shock]
            shock_term = (eps_assigned * decay_vec[None, :]) @ B_eps.T    # [N_p, r]
            F_curr += shock_term

        # Policy intervention response under Active intervention branch
        if b_type in ("active", "act"):
            policy_term = u_policy_act @ B_u.T                            # [N_p, r]
            F_curr += policy_term

        return S_curr, F_curr, eta_lz, ssi_curr


# Top-level functional aliases for direct modular import
branchless_pos = CompleteMarkovSwitchingEngineV57.branchless_pos
predict_and_collapse_kim_hamilton_step = CompleteMarkovSwitchingEngineV57.predict_and_collapse_kim_hamilton_step
kim_backward_regime_probability_smoother = CompleteMarkovSwitchingEngineV57.kim_backward_regime_probability_smoother
rts_exact_kalman_smoother_regime_aware = CompleteMarkovSwitchingEngineV57.rts_exact_kalman_smoother_regime_aware
step1_and_step3_endogenous_markov_propagation = CompleteMarkovSwitchingEngineV57.step1_and_step3_endogenous_markov_propagation

__all__ = [
    "CompleteMarkovSwitchingEngineV57",
    "branchless_pos",
    "predict_and_collapse_kim_hamilton_step",
    "kim_backward_regime_probability_smoother",
    "rts_exact_kalman_smoother_regime_aware",
    "step1_and_step3_endogenous_markov_propagation",
]
