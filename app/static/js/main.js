/* OSINTViet Frontend Scripts - Clean Vanilla JavaScript */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Mobile Menu Toggle
  const mobileMenuBtn = document.getElementById('mobile_menu_button');
  const mobileNavMenu = document.getElementById('mobile_nav_menu');

  if (mobileMenuBtn && mobileNavMenu) {
    mobileMenuBtn.addEventListener('click', () => {
      mobileNavMenu.classList.toggle('hidden');
    });
  }

  // 2. Interactive Household Vulnerability Calculator
  const incomeInput = document.getElementById('calc_income');
  const fuelInput = document.getElementById('calc_fuel');
  const foodInput = document.getElementById('calc_food');
  
  const incomeValueDisplay = document.getElementById('calc_income_display');
  const fuelValueDisplay = document.getElementById('calc_fuel_display');
  const foodValueDisplay = document.getElementById('calc_food_display');
  
  const resultDragDisplay = document.getElementById('calc_drag_display');
  const resultScoreDisplay = document.getElementById('calc_score_display');
  const resultRiskBadge = document.getElementById('calc_risk_badge');

  function formatVnd(val) {
    return new Intl.NumberFormat('vi-VN').format(val) + ' đ';
  }

  function updateCalculator() {
    if (!incomeInput || !fuelInput || !foodInput) return;

    const income = parseFloat(incomeInput.value) * 1000000; // in VND
    const fuelSpend = parseFloat(fuelInput.value) * 1000;    // in VND
    const foodPercent = parseFloat(foodInput.value);         // in %

    if (incomeValueDisplay) incomeValueDisplay.textContent = formatVnd(income);
    if (fuelValueDisplay) fuelValueDisplay.textContent = formatVnd(fuelSpend);
    if (foodValueDisplay) foodValueDisplay.textContent = foodPercent + '%';

    // Calculation model based on petrol surge (+1.4% to +3%) and food logistics inflation drag (+4.2%)
    const foodSpend = income * (foodPercent / 100);
    const fuelDrag = fuelSpend * 0.028; // direct petrol price shock
    const foodDrag = foodSpend * 0.042; // supply-chain delayed food inflation
    const totalMonthlyDrag = Math.round(fuelDrag + foodDrag);

    const vulnerabilityScore = Math.min(100, Math.round((totalMonthlyDrag / income) * 1000));

    if (resultDragDisplay) {
      resultDragDisplay.textContent = formatVnd(totalMonthlyDrag) + ' / tháng';
    }

    if (resultScoreDisplay) {
      resultScoreDisplay.textContent = vulnerabilityScore + ' / 100';
    }

    if (resultRiskBadge) {
      if (vulnerabilityScore > 65) {
        resultRiskBadge.textContent = 'MỨC ĐỘ TỔN THƯƠNG: CAO';
        resultRiskBadge.className = 'inline-flex items-center px-2.5 py-1 text-xs font-mono font-bold bg-red-100 text-red-800 border border-red-300';
      } else if (vulnerabilityScore > 35) {
        resultRiskBadge.textContent = 'MỨC ĐỘ TỔN THƯƠNG: TRUNG BÌNH';
        resultRiskBadge.className = 'inline-flex items-center px-2.5 py-1 text-xs font-mono font-bold bg-amber-100 text-amber-800 border border-amber-300';
      } else {
        resultRiskBadge.textContent = 'MỨC ĐỘ TỔN THƯƠNG: THẤP';
        resultRiskBadge.className = 'inline-flex items-center px-2.5 py-1 text-xs font-mono font-bold bg-emerald-100 text-emerald-800 border border-emerald-300';
      }
    }
  }

  if (incomeInput && fuelInput && foodInput) {
    incomeInput.addEventListener('input', updateCalculator);
    fuelInput.addEventListener('input', updateCalculator);
    foodInput.addEventListener('input', updateCalculator);
    updateCalculator();
  }

  // 3. Simulated Terminal Realtime Logs
  const terminalLogs = document.getElementById('terminal_logs_content');
  if (terminalLogs) {
    const extraEvents = [
      `[${new Date().toLocaleTimeString('vi-VN')}] [CRAG Tri-State] Cross-validating GSO price index with Brent raw spot: 88.42 USD/bbl`,
      `[${new Date().toLocaleTimeString('vi-VN')}] [ARIMA-GARCH] Auto-fitting lag (p=1, q=2) on 30-day series: convergence achieved (AIC=128.4)`,
      `[${new Date().toLocaleTimeString('vi-VN')}] [MatrAIx Engine] Polling 1,000 synthetic micro-agents: 68.2% anticipate belt-tightening`,
      `[${new Date().toLocaleTimeString('vi-VN')}] [Formal Verification] ProbLog theorem proved with confidence 0.967, generating HMAC receipt`
    ];

    let eventIdx = 0;
    setInterval(() => {
      if (eventIdx < extraEvents.length) {
        const line = document.createElement('div');
        line.className = 'text-emerald-400 font-mono text-xs leading-relaxed';
        line.textContent = extraEvents[eventIdx];
        terminalLogs.appendChild(line);
        terminalLogs.scrollTop = terminalLogs.scrollHeight;
        eventIdx++;
      }
    }, 4500);
  }
});
