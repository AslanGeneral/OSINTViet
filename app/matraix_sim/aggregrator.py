import numpy as np
from typing import List
from .survey_task import SurveyResponse

def aggregate_survey_metrics(results: List[SurveyResponse]) -> dict:
    total_agents = len(results)
    cuts_count = sum(1 for r in results if r.will_cut_spending)
    cut_ratio = cuts_count / total_agents if total_agents > 0 else 0.0

    sentiments = np.array([r.sentiment_score for r in results])
    mean_sentiment = float(np.mean(sentiments))
    std_sentiment = float(np.std(sentiments))

    # Tính 95% Confidence Interval cho Sentiment Score
    # CI = Mean +- 1.96 * (std / sqrt(N))
    margin_error = 1.96 * (std_sentiment / np.sqrt(total_agents))
    ci_lower = mean_sentiment - margin_error
    ci_upper = mean_sentiment + margin_error

    # Phân nhóm danh mục bị cắt giảm nhiều nhất
    categories = {}
    for r in results:
        categories[r.cut_category] = categories.get(r.cut_category, 0) + 1

    return {
        "sample_size": total_agents,
        "spending_cut_probability": round(cut_ratio, 4),
        "sentiment_index": {
            "mean": round(mean_sentiment, 4),
            "std": round(std_sentiment, 4),
            "confidence_interval_95": [round(ci_lower, 4), round(ci_upper, 4)]
        },
        "cut_breakdown": categories
    }