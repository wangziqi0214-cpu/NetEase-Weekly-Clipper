"""Deliberately high-recall Rock/Band relevance scorer for music releases."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from song_discovery.models import Release, Track


@dataclass
class ScoringResult:
    """Result of relevance scoring for a candidate track/release."""
    score: float
    is_candidate: bool
    reasons: List[str] = field(default_factory=list)


class RelevanceScorer:
    """
    Evaluates tracks/releases for rock/band relevance with a high-recall policy.
    Retains uncertain candidates for human review, only hard-excluding
    clearly irrelevant utility/spoken/ambient material.
    """

    # Utility, spoken, ASMR, white noise patterns for hard exclusion
    EXCLUSION_KEYWORDS: Set[str] = {
        "有声书", "广播剧", "相声", "评书", "脱口秀", "郭德纲", "单口喜剧",
        "朗读", "有声", "剧场版", "配音", "asmr", "音声", "助眠", "催眠",
        "冥想", "白噪音", "白噪声", "睡眠", "胎教", "儿歌", "睡前故事",
        "早教", "童谣", "幼教", "瑜伽", "疗愈", "心灵", "正念", "自然声",
        "雨声", "流水声", "海浪声", "纯音效", "音效", "环境音", "bgm素材",
        "伴奏带", "伴奏版", "卡拉ok", "减压", "催眠曲", "雷雨声",
    }

    # Band / Group keywords in artist names
    BAND_ARTIST_KEYWORDS: List[str] = [
        "乐队", "band", "乐团", "组合", "三人组", "四人组", "五人组",
        "trio", "quartet", "group", "project", "乐社", "乐坊", "ensemble",
        "orchestra",
    ]

    # Rock / Indie / Alternative genre keywords in title, album, artist
    ROCK_GENRE_KEYWORDS: List[str] = [
        "摇滚", "rock", "朋克", "punk", "金属", "metal", "独立", "indie",
        "民谣", "folk", "后摇", "post-rock", "自赏", "shoegaze", "另类",
        "alternative", "迷幻", "psychedelic", "车库", "garage", "蓝调",
        "blues", "放克", "funk", "硬核", "hardcore", "emo", "ska",
        "grunge", "britpop", "英伦", "噪音", "noise", "synth", "合成器",
        "city pop", "城市流行", "蒸汽波", "vaporwave", "lo-fi", "新浪潮",
        "new wave", "重金属", "流行朋克", "pop punk", "math rock", "数摇",
    ]

    # Live, instrumentation, rehearsal cues
    LIVE_INSTRUMENT_KEYWORDS: List[str] = [
        "live", "现场", "不插电", "unplugged", "吉他", "贝斯", "鼓",
        "巡演", "tour", "demo", "小样", "排练室", "专场", "音乐节",
        "festival", "acoustic", "原声", "重置版", "jam",
    ]

    def is_hard_excluded(self, text_to_check: str) -> Optional[str]:
        """Check if text contains utility/spoken material that should be hard-excluded."""
        lower_text = text_to_check.lower()
        for kw in self.EXCLUSION_KEYWORDS:
            if kw in lower_text:
                return kw
        return None

    def score_candidate(
        self,
        track: Track,
        release: Optional[Release] = None,
    ) -> ScoringResult:
        """
        Compute high-recall relevance score (0.0 - 100.0) and human-readable reasons.
        """
        combined_text = " ".join([
            track.title,
            track.album_title,
            track.artist_names_str,
            release.title if release else "",
            release.artist_names_str if release else "",
        ])

        # 1. Hard Exclusion Check
        matched_exclusion = self.is_hard_excluded(combined_text)
        if matched_exclusion:
            return ScoringResult(
                score=5.0,
                is_candidate=False,
                reasons=[f"排除: 检测到非音乐/功能性音频关键词 ('{matched_exclusion}')"],
            )

        score = 50.0
        reasons = ["基准分: 标准音乐发行 (高召回保留待审)"]

        # 2. Artist Band Indicators (+30)
        artist_lower = track.artist_names_str.lower()
        band_matches = [kw for kw in self.BAND_ARTIST_KEYWORDS if kw in artist_lower]
        # Check band naming pattern (e.g. "The ...", "... & The ...")
        the_pattern = bool(re.search(r"\bthe\s+\w+", artist_lower) or re.search(r"&\s*the\b", artist_lower))
        if band_matches or the_pattern:
            matches_str = ", ".join(band_matches) if band_matches else "The / & The 命名形态"
            score += 30.0
            reasons.append(f"命中乐队/乐团特征 (+30分): {matches_str}")

        # 3. Rock / Subgenre Keywords in Title / Album / Artist (+25)
        text_lower = combined_text.lower()
        rock_matches = [kw for kw in self.ROCK_GENRE_KEYWORDS if kw in text_lower]
        if rock_matches:
            # Deduplicate case-insensitively
            unique_matches = list(dict.fromkeys(rock_matches))[:5]
            score += 25.0
            reasons.append(f"命中摇滚/独立/流派关键词 (+25分): {', '.join(unique_matches)}")

        # 4. Live / Instrument / Tour Cues (+15)
        live_matches = [kw for kw in self.LIVE_INSTRUMENT_KEYWORDS if kw in text_lower]
        if live_matches:
            unique_live = list(dict.fromkeys(live_matches))[:3]
            score += 15.0
            reasons.append(f"包含现场/乐器/巡演特征 (+15分): {', '.join(unique_live)}")

        # 5. Multi-Artist Collaboration (+10)
        if len(track.artists) >= 2 or "/" in track.artist_names_str or "feat" in text_lower:
            score += 10.0
            reasons.append("多人/合作音乐人参与 (+10分)")

        # Cap score
        final_score = min(100.0, max(0.0, score))
        is_cand = final_score >= 30.0

        return ScoringResult(
            score=final_score,
            is_candidate=is_cand,
            reasons=reasons,
        )
