"""Streamlit High-Density Editorial Review Table, Preference Active Learning & One-Click NetEase Playlist Publishing."""

import json
import os
import sys
import hashlib
from typing import Any, Dict, List

# Ensure package is discoverable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from song_discovery.db import DiscoveryDB
from song_discovery.models import REJECTION_REASON_LABELS, REASON_SCOPE_LABELS
from song_discovery.publisher import NetEasePublisher
from song_discovery.netease_service import NetEaseServiceManager
from song_discovery.review_helpers import (
    apply_machine_filter_to_legacy_pending,
    build_batch_decision_updates,
    compute_preview_signature,
    deduplicate_candidates,
    extract_selected_row_indices,
    filter_candidates_by_pool_and_criteria,
    generate_default_weekly_playlist_name,
    get_active_preference_model,
    get_platforms_overview,
    get_publication_readiness,
    is_incomplete_kkbox_candidate,
    maybe_train_preference_model_from_db,
    prepare_editor_rows,
    resolve_editor_decisions,
    schedule_refresh_command,
    train_preference_model_from_db,
)

try:
    import streamlit as st
    import pandas as pd
except ImportError:
    print("Streamlit is not installed in the current environment. Please install streamlit (e.g. pip install streamlit) to run this web UI.")
    sys.exit(1)


def load_supervisor_state(state_path: str = "output/supervisor_state.json") -> dict:
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    st.set_page_config(
        page_title="华语新歌编辑部审听工作台",
        page_icon="🎸",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("🎸 华语新歌审听与偏好学习工作台 (Editorial Workbench & Active Learner)")
    st.caption("高密度表格审听流、单用户偏好闭环反馈、结构化否决原因、三平台平等数据源与网易云周刊歌单发布中枢")

    # Sidebar controls
    st.sidebar.header("⚙️ 路径与系统配置")
    db_path = st.sidebar.text_input("SQLite 数据库路径", value="output/discovery.db")
    state_path = st.sidebar.text_input("守护进程状态路径", value="output/supervisor_state.json")
    cookie_file = st.sidebar.text_input("网易云 Cookie 文件", value="cookie.txt")
    netease_api_url = st.sidebar.text_input("网易云本地 API 地址", value="http://localhost:3000")

    if not os.path.exists(db_path):
        st.warning(f"数据库文件未找到: `{db_path}`。请先启动守护进程或运行采集命令生成候选数据。")
        return

    db = DiscoveryDB(db_path=db_path)
    supervisor_state = load_supervisor_state(state_path)
    platforms_info = get_platforms_overview(db=db, state=supervisor_state)
    active_model = get_active_preference_model(db)

    def run_auto_learning_after_review() -> None:
        result = maybe_train_preference_model_from_db(db)
        if result.get("attempted") and result.get("activated"):
            st.success(f"🧠 已自动训练并启用新偏好模型 `{result['model_version']}`。")
        elif result.get("attempted"):
            st.warning("🧠 新模型未通过安全门，系统继续沿用原模型。")

    # =========================================================================
    # Section 1: Top 3-Platform Data Source Equal Status Cards & Unified Refresh
    # =========================================================================
    st.markdown("### 📡 三个平台状态")
    col_qq, col_ne, col_kk = st.columns(3)

    qq_data = platforms_info["platforms"]["qq"]
    with col_qq:
        st.subheader("🐧 QQ 音乐 (QQ Music)")
        st.metric("入库候选曲目", f"{qq_data['candidates_count']} 首")
        st.caption(f"**健康状态**: `{qq_data['health']}` | **最近采集**: `{qq_data['last_run_time']}`")

    ne_data = platforms_info["platforms"]["netease"]
    with col_ne:
        st.subheader("🔴 网易云音乐 (NetEase)")
        st.metric("入库候选曲目", f"{ne_data['candidates_count']} 首")
        st.caption(f"**健康状态**: `{ne_data['health']}` | **最近采集**: `{ne_data['last_run_time']}`")

    kk_data = platforms_info["platforms"]["kkbox"]
    with col_kk:
        st.subheader("📻 KKBOX")
        st.metric("入库候选曲目", f"{kk_data['candidates_count']} 首")
        st.caption(f"**健康状态**: `{kk_data['health']}` | **最近同步**: `{kk_data['last_run_time']}`")

    # Unified Refresh Button
    col_btn_refresh, col_refresh_status = st.columns([1, 3])
    with col_btn_refresh:
        btn_refresh_all = st.button("🔄 立即更新三平台", type="primary", use_container_width=True)
    with col_refresh_status:
        if btn_refresh_all:
            cmd = schedule_refresh_command()
            st.success(f"✅ 已提交三平台更新 (`{cmd['command_id']}`)，后台正在处理。")
        else:
            st.info("💡 点击一次即可同时更新 QQ 音乐、网易云音乐和 KKBOX。")

    st.markdown("---")

    # =========================================================================
    # Section 2: Active Learning Model Registry & Feedback Statistics
    # =========================================================================
    fb_stats = db.get_feedback_stats()
    all_models = db.get_all_model_versions()

    st.markdown("### 🧠 个人偏好学习闭环与模型状态 (Single-User Preference Active Learning)")
    with st.expander("📊 查看偏好模型学习状态、门槛进度与版本管理", expanded=not active_model.is_cold_start):
        col_m_info1, col_m_info2, col_m_info3, col_m_info4 = st.columns(4)
        col_m_info1.metric("已累积有效反馈", f"{fb_stats.get('total', 0)} / 60")
        col_m_info2.metric("通过样本 (Positive)", f"{fb_stats.get('approved', 0)} / 15")
        col_m_info3.metric("排除样本 (Negative)", f"{fb_stats.get('rejected', 0)} / 15")
        col_m_info4.metric("当前活跃模型", active_model.version_id)

        # Cold start / Missing sample explanation
        if active_model.is_cold_start:
            missing_total = max(0, 60 - fb_stats.get("total", 0))
            missing_pos = max(0, 15 - fb_stats.get("approved", 0))
            missing_neg = max(0, 15 - fb_stats.get("rejected", 0))
            st.warning(
                f"❄️ **学习尚未启用 (冷启动中)**：样本不足，仅做保守统计记忆与基准打分。"
                f"还差 **{missing_total}** 条有效反馈（至少还需 **{missing_pos}** 条通过、**{missing_neg}** 条排除）即可激活个人偏好自适应模型。"
            )
        else:
            metrics = active_model.metrics or {}
            eval_rec = metrics.get("eval_recall", 1.0)
            eval_prec = metrics.get("eval_precision", 0.0)
            eval_f1 = metrics.get("eval_f1", 0.0)
            st.success(
                f"🔥 **个性化偏好模型已激活 (`{active_model.version_id}`)** | "
                f"分组交叉验证召回率: **{eval_rec:.1%}** (安全门约束: ≥95%) | 精度: **{eval_prec:.1%}** | F1: **{eval_f1:.3f}**"
            )

        # Model Retraining & Rollback UI
        col_train, col_rb_sel, col_rb_btn = st.columns([1.5, 2, 1])
        with col_train:
            if st.button("🚀 训练/更新偏好模型 (Grouped CV 评估)", use_container_width=True):
                new_model, summary = train_preference_model_from_db(db)
                if summary.get("activated"):
                    st.success(f"🎉 成功训练并激活新模型 `{new_model.version_id}`！召回率: {new_model.metrics.get('eval_recall'):.1%}")
                    st.experimental_rerun() if hasattr(st, "experimental_rerun") else st.rerun()
                elif summary.get("success"):
                    failed_gate = "95% 召回安全门" if not summary.get("recall_gate_passed") else "旧新模型精度对比"
                    st.warning(f"新模型已完成评估，但未通过{failed_gate}，因此没有激活，当前模型保持不变。")
                else:
                    st.error(f"无法训练模型: {summary.get('criteria', {}).get('message', '样本量未达门槛')}")

        with col_rb_sel:
            version_options = [m["version_id"] for m in all_models]
            selected_rb_version = st.selectbox(
                "历史模型版本回滚",
                options=version_options if version_options else ["(暂无已保存版本)"],
                index=0 if version_options else 0,
            )

        with col_rb_btn:
            if st.button("⏪ 确认回滚", use_container_width=True, disabled=not version_options):
                if selected_rb_version and selected_rb_version in version_options:
                    db.rollback_model_version(selected_rb_version)
                    st.success(f"已回滚活跃模型至: `{selected_rb_version}`")
                    st.experimental_rerun() if hasattr(st, "experimental_rerun") else st.rerun()

    st.markdown("---")

    # =========================================================================
    # Section 3: Candidate Tiering & Filter Toolbar
    # =========================================================================
    apply_machine_filter_to_legacy_pending(db)
    all_raw_candidates = db.get_candidates(limit=10000)
    incomplete_kkbox_count = sum(
        1 for candidate in all_raw_candidates if is_incomplete_kkbox_candidate(candidate)
    )
    reviewable_candidates = [
        candidate for candidate in all_raw_candidates
        if not is_incomplete_kkbox_candidate(candidate)
    ]

    # Compute the actual personalized three-pool counts shown in the table.
    pool_counts = {"primary": 0, "uncertain": 0, "machine_filtered": 0}
    for candidate in reviewable_candidates:
        pool_counts[active_model.score_candidate(candidate).pool] += 1

    st.markdown("### 🎛️ 审听池分层与快速筛选")
    col_pool, col_band, col_plat, col_status = st.columns([2, 1.2, 1, 1])

    with col_pool:
        pool_choice = st.radio(
            "选择审听池 (机筛分层与偏好推荐)",
            options=["primary", "uncertain", "machine_filtered", "all"],
            format_func=lambda x: {
                "primary": f"🎯 主审核池（{pool_counts['primary']} 首）",
                "uncertain": f"⚠️ 不确定/探索池（{pool_counts['uncertain']} 首）",
                "machine_filtered": f"🚫 机器排除池（{pool_counts['machine_filtered']} 首，可恢复）",
                "all": f"🌐 全部有效候选 ({len(reviewable_candidates)} 首)",
            }[x],
            index=0,
            horizontal=True,
        )

    with col_band:
        st.write("")  # alignment
        only_band_toggle = st.checkbox("🎸 仅看乐队 / 摇滚独立倾向", value=False)

    with col_plat:
        plat_filter = st.selectbox("平台过滤", options=["all", "qq", "netease", "kkbox"], index=0)

    with col_status:
        status_filter = st.selectbox(
            "审核状态",
            options=["to_review", "approved", "rejected", "deferred", "all"],
            format_func=lambda x: {
                "to_review": "待我处理",
                "approved": "已通过",
                "rejected": "已排除",
                "deferred": "存疑",
                "all": "全部状态",
            }[x],
            index=0,
        )

    col_search, col_score = st.columns([3, 1])
    with col_search:
        search_kw = st.text_input("🔍 关键词快速检索 (歌曲名 / 歌手 / 专辑)", value="").strip()
    with col_score:
        min_score_val = st.slider("最低评分阈值", min_value=0.0, max_value=100.0, value=0.0, step=5.0)

    # Filter raw candidates
    filtered_cands = filter_candidates_by_pool_and_criteria(
        candidates=reviewable_candidates,
        pool=pool_choice,
        only_band_rock=only_band_toggle,
        platform=plat_filter if plat_filter != "all" else None,
        min_score=min_score_val,
        search_keyword=search_kw,
        model=active_model,
    )

    if status_filter == "to_review":
        if pool_choice == "primary":
            actionable_statuses = {"pending"}
        elif pool_choice == "machine_filtered":
            actionable_statuses = {"machine_filtered"}
        else:
            actionable_statuses = {"pending", "machine_filtered"}
        filtered_cands = [c for c in filtered_cands if c.get("review_status") in actionable_statuses]
    elif status_filter != "all":
        filtered_cands = [c for c in filtered_cands if c.get("review_status") == status_filter]

    # Only collapse duplicates in the human-review view. The original platform
    # records remain in SQLite for traceability and publishing resolution.
    raw_filtered_count = len(filtered_cands)
    filtered_cands = deduplicate_candidates(filtered_cands, all_candidates=reviewable_candidates)
    merged_duplicate_count = raw_filtered_count - len(filtered_cands)
    all_deduped_candidates = deduplicate_candidates(reviewable_candidates)
    all_deduped_by_key = {
        item["canonical_key"]: item for item in all_deduped_candidates
    }

    # Metrics Summary in Sidebar
    stats = db.get_stats()
    st.sidebar.subheader("📊 全库审核状态统计")
    st.sidebar.metric("待审核 (Pending)", stats.get("pending", 0))
    st.sidebar.metric("已通过 (Approved)", stats.get("approved", 0))
    st.sidebar.metric("已排除 (Rejected)", stats.get("rejected", 0))
    st.sidebar.metric("暂缓/存疑 (Deferred)", stats.get("deferred", 0))
    st.sidebar.metric("机器排除（可恢复）", stats.get("machine_filtered", 0))
    st.sidebar.metric("全库总候选", stats.get("total_candidates", 0))

    # =========================================================================
    # Section 4: High-Density Table with Native Multi-Row/Cell Selection & Form Below
    # =========================================================================
    st.markdown(
        f"#### 📋 候选曲目审听列表（原始 **{raw_filtered_count}** 条，"
        f"去重后 **{len(filtered_cands)}** 首，已合并 **{merged_duplicate_count}** 条重复）"
    )
    if incomplete_kkbox_count:
        st.caption(
            f"已自动隔离 {incomplete_kkbox_count} 条旧版 KKBOX 占位记录，"
            "不会进入人工审核或批量操作。"
        )

    if "table_generation" not in st.session_state:
        st.session_state["table_generation"] = 0
    if "batch_selected_keys" not in st.session_state:
        st.session_state["batch_selected_keys"] = set()
    batch_selected_keys = set(st.session_state["batch_selected_keys"])

    # 1. Native Selectable Dataframe Table (Table First)
    if filtered_cands:
        editor_rows = prepare_editor_rows(
            filtered_cands,
            model=active_model,
            selected_ids=batch_selected_keys,
        )
        df_editor = pd.DataFrame(editor_rows)

        column_config = {
            "id": None,
            "选择": None,
            "_canonical_key": None,
            "_raw_ids": None,
            "_original_status": None,
            "_original_reason": None,
            "_original_scope": None,
            "_original_notes": None,
            "批选": st.column_config.TextColumn("已勾选", width="small"),
            "当前状态": st.column_config.TextColumn("当前状态", width="small"),
            "否决原因": st.column_config.TextColumn("否决原因", width="medium"),
            "否决范围": st.column_config.TextColumn("否决范围", width="small"),
            "备注": st.column_config.TextColumn("备注", width="medium"),
            "平台": st.column_config.TextColumn("平台", width="small"),
            "歌曲": st.column_config.TextColumn("歌曲名", width="medium"),
            "艺人": st.column_config.TextColumn("艺人/乐队", width="medium"),
            "发行专辑/单曲": st.column_config.TextColumn("所属发行", width="medium"),
            "发行日期": st.column_config.TextColumn("发行日期", width="small"),
            "类型": st.column_config.TextColumn("类型", width="small"),
            "音轨": st.column_config.NumberColumn("音轨", width="small"),
            "评分": st.column_config.NumberColumn("个性化评分", format="%.1f", width="small"),
            "审听池": st.column_config.TextColumn("审听池", width="medium"),
            "判定依据与解释": st.column_config.TextColumn("判定依据与解释", width="large"),
            "模型版本": st.column_config.TextColumn("模型版本", width="small"),
            "链接": st.column_config.LinkColumn("直达链接", display_text="🔗 查看", width="small"),
        }

        editor_key_seed = "|".join([
            pool_choice, str(only_band_toggle), plat_filter, status_filter,
            str(min_score_val), search_kw, active_model.version_id,
            str(st.session_state["table_generation"]),
        ])
        editor_key = "review_native_df_" + hashlib.sha1(editor_key_seed.encode("utf-8")).hexdigest()[:10]

        table_event = st.dataframe(
            df_editor,
            column_config=column_config,
            width="stretch",
            hide_index=True,
            height=min(550, 38 * len(editor_rows) + 60),
            key=editor_key,
            on_select="rerun",
            selection_mode=["multi-row", "multi-cell"],
            row_height=36,
        )

        # Native row selection and rectangular cell selection both identify
        # rows. The user explicitly commits that temporary range into the
        # persistent batch set with the buttons below.
        selected_row_indices = extract_selected_row_indices(table_event, len(editor_rows))
        range_keys = {
            editor_rows[index]["_canonical_key"] for index in selected_row_indices
        }

        col_add, col_remove, col_all, col_clear = st.columns([1.5, 1.5, 1.35, 1.2])
        with col_add:
            if st.button(
                f"☑️ 勾选所选区域（{len(range_keys)} 首）",
                type="primary",
                use_container_width=True,
                disabled=not range_keys,
            ):
                st.session_state["batch_selected_keys"] = batch_selected_keys | range_keys
                st.session_state["table_generation"] += 1
                st.rerun()
        with col_remove:
            if st.button(
                "⬜ 取消勾选所选区域",
                use_container_width=True,
                disabled=not range_keys,
            ):
                st.session_state["batch_selected_keys"] = batch_selected_keys - range_keys
                st.session_state["table_generation"] += 1
                st.rerun()
        with col_all:
            if st.button(
                f"☑️ 全选当前结果（{len(editor_rows)} 首）",
                use_container_width=True,
                disabled=not editor_rows,
            ):
                st.session_state["batch_selected_keys"] = batch_selected_keys | {
                    row["_canonical_key"] for row in editor_rows
                }
                st.session_state["table_generation"] += 1
                st.rerun()
        with col_clear:
            if st.button(
                "🗑️ 清空所有批选",
                use_container_width=True,
                disabled=not batch_selected_keys,
            ):
                st.session_state["batch_selected_keys"] = set()
                st.session_state["table_generation"] += 1
                st.rerun()

        selected_groups = [
            all_deduped_by_key[key]
            for key in batch_selected_keys
            if key in all_deduped_by_key
        ]
        selected_candidate_ids = [int(group["id"]) for group in selected_groups]
        selected_raw_ids = sorted({
            int(raw_id)
            for group in selected_groups
            for raw_id in group.get("raw_ids", [group["id"]])
        })
        num_selected = len(selected_groups)

        if num_selected:
            st.info(
                f"已批选 **{num_selected}** 首歌（对应 {len(selected_raw_ids)} 条平台记录）。"
                "可以继续框选其他区域并追加，然后在下方统一登记。"
            )
        else:
            st.caption(
                "用鼠标框选任意单元格区域，或点左侧选择行；然后点「勾选所选区域」。"
                "可以分多次框选追加。"
            )

        st.write("")  # visual spacing

        # 2. Unified Review Registration Form (st.form Placed Directly Below Table)
        with st.form(key="unified_review_submission_form", clear_on_submit=False):
            st.markdown(f"##### 🎯 统一审核登记表单 (已选 **{num_selected}** 首曲目)")
            col_dec, col_submit = st.columns([3, 2])

            with col_dec:
                form_decision = st.selectbox(
                    "审核决定",
                    options=["approved", "rejected", "deferred", "pending"],
                    format_func=lambda x: {
                        "approved": "✅ 通过 (Approved)",
                        "rejected": "🚫 排除 (Rejected)",
                        "deferred": "⚠️ 存疑 (Deferred)",
                        "pending": "🔄 重置为待审 (Pending)",
                    }[x],
                    index=0,
                )

            with col_submit:
                st.write("")  # alignment
                submit_btn_label = f"⚡ 提交审核决定并应用到所选 ({num_selected} 首)" if num_selected > 0 else "⚡ 提交审核决定"
                form_submitted = st.form_submit_button(
                    submit_btn_label,
                    type="primary",
                    use_container_width=True,
                    disabled=(num_selected == 0),
                )

            with st.expander("🛠️ 可选：进一步教系统（细化否决原因、影响范围与备注）", expanded=False):
                col_reason, col_scope, col_note = st.columns([2.5, 1.8, 3])
                with col_reason:
                    form_reject_reason = st.selectbox(
                        "细化否决原因 (默认: 泛化不喜欢/不符合选歌偏好)",
                        options=["other"] + [k for k in REJECTION_REASON_LABELS.keys() if k != "other"],
                        format_func=lambda x: f"🚫 {REJECTION_REASON_LABELS[x]}",
                        index=0,
                    )
                with col_scope:
                    form_reject_scope = st.selectbox(
                        "否决影响范围",
                        options=list(REASON_SCOPE_LABELS.keys()),
                        format_func=lambda x: REASON_SCOPE_LABELS[x],
                        index=0,
                    )
                with col_note:
                    form_notes = st.text_input(
                        "审核补充备注",
                        placeholder="例如：主唱非摇滚声线 / 伴奏 / 巡演现场",
                    )

            if form_submitted:
                if num_selected == 0:
                    st.warning("⚠️ 请先在上方表格中勾选要审核的曲目，或点击【全选当前筛选结果】！")
                else:
                    # One canonical song produces one learning sample. Its
                    # duplicate platform rows are synchronized without being
                    # counted repeatedly as separate human judgements.
                    updates = build_batch_decision_updates(
                        selected_candidate_ids=selected_candidate_ids,
                        decision=form_decision,
                        reason_code=form_reject_reason if form_decision == "rejected" else None,
                        reason_scope=form_reject_scope if form_decision == "rejected" else "track",
                        notes=form_notes if form_notes else "",
                        active_model_version=active_model.version_id,
                    )
                    updated_count = db.bulk_update_review_status(updates)
                    duplicate_ids = sorted(set(selected_raw_ids) - set(selected_candidate_ids))
                    synced_count = db.bulk_sync_review_status(
                        candidate_ids=duplicate_ids,
                        status=form_decision,
                        notes=form_notes if form_notes else "",
                    )
                    run_auto_learning_after_review()
                    st.session_state["batch_selected_keys"] = set()
                    st.session_state["table_generation"] += 1
                    dec_label = {
                        "approved": "通过",
                        "rejected": f"排除（{REJECTION_REASON_LABELS.get(form_reject_reason, '泛化不喜欢')}）",
                        "deferred": "存疑",
                        "pending": "待审核",
                    }[form_decision]
                    st.success(
                        f"🎉 已将 {updated_count} 首去重歌曲标记为【{dec_label}】，"
                        f"并同步 {synced_count} 条重复平台记录。"
                    )
                    st.experimental_rerun() if hasattr(st, "experimental_rerun") else st.rerun()

    else:
        st.info("当前筛选条件下没有候选曲目。")

    st.markdown("---")

    # =========================================================================
    # Section 5: Human Review Completion & NetEase Playlist Publishing
    # =========================================================================
    st.markdown("## 🚀 完成本周审核并创建网易云歌单")
    st.caption("必须完成全部待审核曲目（Pending = 0）并经过 Dry-Run 预演确认后，方可正式发布至网易云周刊歌单。")

    readiness = get_publication_readiness(db)

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("待审核 (Pending)", readiness["pending_count"])
    col_m2.metric("已通过 (Approved)", readiness["approved_count"])
    col_m3.metric("机器排除（可恢复）", readiness["machine_filtered_count"])
    col_m4.metric("总候选数", readiness["total_candidates"])

    if readiness["pending_count"] > 0:
        st.warning(f"⚠️ {readiness['message']}")
    else:
        st.success(f"✅ {readiness['message']}")

    col_pub_cfg1, col_pub_cfg2 = st.columns([2, 1])
    with col_pub_cfg1:
        default_pl_name = generate_default_weekly_playlist_name()
        playlist_name_input = st.text_input("网易云歌单名称", value=default_pl_name)
    with col_pub_cfg2:
        explicit_pl_id = st.text_input("指定已有歌单 ID (留空则自动查找/创建)", value="")

    approved_candidates = db.get_candidates(status="approved", limit=500)
    approved_ids = [c["id"] for c in approved_candidates]
    current_preview_sig = compute_preview_signature(approved_ids, playlist_name_input)

    session_preview_sig = st.session_state.get("preview_signature")
    is_preview_valid = bool(session_preview_sig and session_preview_sig == current_preview_sig)

    can_live_publish = (readiness["is_ready"] and is_preview_valid)

    if readiness["is_ready"] and not is_preview_valid:
        st.info("💡 审核已完成。请先点击 **「🔍 预览发布规划 (Dry Run 预演)」** 进行匹配校验，即可解锁正式发布按钮。")

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        btn_dry_run = st.button("🔍 预览发布规划 (Dry Run 预演)", use_container_width=True)
    with col_btn2:
        btn_live_pub = st.button(
            "🎵 确认发布至网易云歌单",
            type="primary",
            disabled=not can_live_publish,
            use_container_width=True,
        )

    # 1. Handle Dry Run Preview
    if btn_dry_run:
        with st.spinner("正在启动本地 API 服务并解析跨平台曲目匹配..."):
            try:
                svc = NetEaseServiceManager(base_url=netease_api_url, auto_start=True)
                svc.start()

                publisher = NetEasePublisher(base_url=netease_api_url, db=db)
                dry_res = publisher.publish_approved(
                    playlist_name=playlist_name_input,
                    playlist_id=explicit_pl_id or None,
                    cookie_file=cookie_file,
                    dry_run=True,
                )

                st.session_state["preview_signature"] = current_preview_sig
                st.session_state["last_dry_res"] = dry_res

                st.subheader("📋 发布预演结果 (Dry-Run Preview)")
                st.write(f"- **目标歌单**: `{dry_res.get('playlist_name')}` (ID: `{dry_res.get('playlist_id')}`)")
                st.write(f"- **审核通过曲目**: **{dry_res.get('total_approved')}** 首")
                st.write(f"- **原生网易云曲目 (Direct)**: **{dry_res.get('direct_netease_count')}** 首")
                st.write(f"- **跨平台保守匹配 (Matched)**: **{dry_res.get('matched_count')}** 首")
                st.write(f"- **存疑/未匹配跳过**: 存疑 **{dry_res.get('ambiguous_count')}** 首, 未匹配 **{dry_res.get('unmatched_count')}** 首")
                st.write(f"- **计划入库曲目数**: **{dry_res.get('ready_to_add_count')}** 首 (已存在: {dry_res.get('already_in_playlist_count')} 首)")

                if dry_res.get("resolved_items"):
                    st.dataframe([
                        {
                            "标题": item["track_title"],
                            "歌手": item["artist_names"],
                            "来源平台": item["platform"].upper(),
                            "匹配结果": item["match_status"],
                            "网易云TrackID": item.get("netease_track_id") or "-",
                            "置信度": f"{item.get('match_confidence', 0):.2f}",
                        }
                        for item in dry_res["resolved_items"]
                    ])
                st.experimental_rerun() if hasattr(st, "experimental_rerun") else st.rerun()
            except Exception as exc:
                st.error(f"预演过程发生异常: {exc}")

    # 2. Handle Live Publishing
    if btn_live_pub:
        with st.spinner("正在发布至网易云音乐..."):
            try:
                svc = NetEaseServiceManager(base_url=netease_api_url, auto_start=True)
                svc.start()

                publisher = NetEasePublisher(base_url=netease_api_url, db=db)
                live_res = publisher.publish_approved(
                    playlist_name=playlist_name_input,
                    playlist_id=explicit_pl_id or None,
                    cookie_file=cookie_file,
                    dry_run=False,
                )

                st.session_state["preview_signature"] = None

                st.success("🎉 **歌单发布成功！**")
                st.markdown(
                    f"### 🔗 [点击前往网易云查看歌单: {live_res.get('playlist_name')}]({live_res.get('playlist_url')})"
                )
                st.write(f"- **歌单 ID**: `{live_res.get('playlist_id')}`")
                st.write(f"- **本次新追加曲目**: **{live_res.get('added_count')}** 首")
                st.write(f"- **发布人**: `{live_res.get('publisher_user') or '网易云登录用户'}`")
                st.write(f"- **直接复用**: {live_res.get('direct_netease_count')} 首 | **跨平台匹配**: {live_res.get('matched_count')} 首")
                if live_res.get("ambiguous_count") or live_res.get("unmatched_count"):
                    st.info(f"存疑与未匹配曲目共 {live_res.get('ambiguous_count', 0) + live_res.get('unmatched_count', 0)} 首已安全跳过，未误加错歌。")

            except Exception as exc:
                st.error(f"发布失败: {exc}")


if __name__ == "__main__":
    main()
