import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronsDown,
  ChevronsUp,
  Clock,
  Copy,
  Download,
  ExternalLink,
  FileText,
  GripVertical,
  History,
  Link as LinkIcon,
  Loader2,
  MoveDown,
  MoveUp,
  RefreshCw,
  Rocket,
  RotateCcw,
  Save,
  Search,
  SearchX,
  ShieldAlert,
  Trash2,
  X,
} from 'lucide-react';
import {
  deleteManualMatch,
  excludeDeliverySong,
  exportWeeklyRelease,
  fetchLatestVideoWorkflow,
  fetchPublicationPreview,
  fetchPublishCopy,
  fetchVideoWorkflowJobs,
  publishWeeklyRelease,
  resetPublishCopy,
  restoreDeliverySong,
  resumeVideoWorkflowJob,
  savePublicationOrder,
  savePublishCopy,
  searchNetEaseSongs,
  setManualMatch,
} from './api';
import { ArtistHoverTrigger } from './ArtistHoverCard';
import { NetEaseSearchItem, PublicationItem, PublicationPreview, PublishCopyData, VideoWorkflowJob } from './types';
export default function DeliveryWorkspace({ onOpenSafety }: { onOpenSafety: () => void }) {
  const [preview, setPreview] = useState<PublicationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<'export' | 'publish' | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [ordered, setOrdered] = useState<PublicationItem[]>([]);
  const [dragId, setDragId] = useState<number | null>(null);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved');
  const [videoJob, setVideoJob] = useState<VideoWorkflowJob | null>(null);
  const [showCopyPanel, setShowCopyPanel] = useState(false);
  const [showTaskCenter, setShowTaskCenter] = useState(false);
  const [matchingItem, setMatchingItem] = useState<PublicationItem | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const value = await fetchPublicationPreview();
      setPreview(value);
      setOrdered(value.ready_items);
    } catch (err) {
      setError(err instanceof Error ? err.message : '发布预演失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const poll = () =>
      void fetchLatestVideoWorkflow()
        .then(x => setVideoJob(x.job || null))
        .catch(() => {});
    poll();
    const timer = window.setInterval(poll, 4000);
    return () => window.clearInterval(timer);
  }, []);

  const persist = async (items: PublicationItem[]) => {
    setSaveState('saving');
    try {
      await savePublicationOrder(items.map(x => x.candidate_id));
      setSaveState('saved');
    } catch {
      setSaveState('error');
    }
  };

  const move = (from: number, to: number) => {
    if (from === to || to < 0 || to >= ordered.length) return;
    const next = [...ordered];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setOrdered(next);
    void persist(next);
  };

  const dropOn = (target: number) => {
    const from = ordered.findIndex(x => x.candidate_id === dragId);
    setDragId(null);
    if (from >= 0) move(from, target);
  };

  const handleClearOverride = async (item: PublicationItem) => {
    if (!window.confirm(`确认清除歌曲《${item.track_title}》的人工匹配？将恢复自动匹配判断。`)) return;
    try {
      await deleteManualMatch(item.candidate_id);
      setMessage(`已清除《${item.track_title}》的人工匹配`);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '清除人工匹配失败');
    }
  };

  const handleExclude = async (item: PublicationItem) => {
    if (
      !window.confirm(
        `确认将《${item.track_title}》从本周交付中移除？\n\n该歌曲将保留在已通过候选库中，但会持久从本期交付中排除（刷新页面不会重新添加）。`
      )
    ) {
      return;
    }
    try {
      await excludeDeliverySong(item.candidate_id, preview?.playlist_name);
      setMessage(`已将《${item.track_title}》从本周交付中移除`);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '移除失败');
    }
  };

  const handleRestore = async (item: PublicationItem) => {
    try {
      await restoreDeliverySong(item.candidate_id, preview?.playlist_name);
      setMessage(`已将《${item.track_title}》恢复至本周交付`);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '恢复失败');
    }
  };

  const unresolved = (preview?.ambiguous_count || 0) + (preview?.unmatched_count || 0);

  const exportRelease = async () => {
    setAction('export');
    setError('');
    try {
      const result = await exportWeeklyRelease();
      setMessage(`物料已导出：${result.path}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '导出失败');
    } finally {
      setAction(null);
    }
  };

  const publish = async () => {
    if (!preview) return;
    const note = `将按当前顺序发布 ${ordered.length} 首${unresolved ? `，跳过 ${unresolved} 首未解决歌曲` : ''}，随后自动启动视频工作流。`;
    if (!window.confirm(`${note}\n\n歌单：${preview.playlist_name}\n此操作会修改你的网易云账号并启动本地视频任务，确认继续？`)) return;
    setAction('publish');
    setError('');
    try {
      await persist(ordered);
      const result = await publishWeeklyRelease(
        preview.playlist_name,
        unresolved > 0,
        ordered.map(x => x.candidate_id)
      );
      setVideoJob(result.video_workflow || null);
      setMessage(`歌单已发布并交接视频工作流：${result.playlist_url || ''}`);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '发布失败');
    } finally {
      setAction(null);
    }
  };

  return (
    <main className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#090a0f]">
      <div className="flex items-center justify-between border-b border-[#232736] bg-[#11131b] px-6 py-4">
        <div>
          <div className="flex items-center gap-2">
            <Rocket className="h-5 w-5 text-violet-400" />
            <h1 className="text-base font-semibold">本周交付</h1>
            {preview && (
              <span className="rounded border border-emerald-700/60 bg-emerald-950/40 px-2 py-0.5 font-mono text-[10px] text-emerald-300">
                {preview.ready_count} WEEK-READY
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {preview ? `发行硬门 ${preview.release_window.start} → ${preview.release_window.end}` : '审核结果 → 发行日期核验 → 正式歌单 → 视频'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowTaskCenter(true)}
            className="flex items-center gap-1.5 rounded border border-cyan-800/70 bg-cyan-950/30 px-3 py-2 text-xs font-medium text-cyan-300 hover:bg-cyan-950/60 transition"
          >
            <History className="h-4 w-4 text-cyan-400" />
            <span>视频任务中心</span>
          </button>
          <button
            onClick={onOpenSafety}
            className="flex items-center gap-2 rounded border border-rose-800/70 bg-rose-950/30 px-3 py-2 text-xs text-rose-300 hover:bg-rose-950/60 transition"
          >
            <ShieldAlert className="h-4 w-4" />
            <span>机筛安全门：影子阻断</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-sm text-violet-300">
          <Loader2 className="h-5 w-5 animate-spin" />正在用网易云目录核对已通过歌曲…
        </div>
      ) : error && !preview ? (
        <Failure text={error} retry={reload} />
      ) : (
        preview && (
          <>
            <section className="grid grid-cols-4 gap-3 px-6 py-4">
              <Metric
                label="可直接发布"
                value={preview.ready_count}
                note={`网易云直达 ${preview.direct_netease_count} · 跨平台匹配 ${preview.matched_count}`}
                tone="emerald"
              />
              <Metric label="匹配存疑" value={preview.ambiguous_count} note="点击卡片可人工搜索并指定匹配" tone="amber" />
              <Metric label="网易云未找到" value={preview.unmatched_count} note="支持按歌名/链接人工匹配" tone="rose" />
              <Metric label="非本周隔离" value={preview.out_of_week_count} note="不进入歌单与视频" tone="violet" />
            </section>

            <div className="flex min-h-0 flex-1 gap-4 px-6 pb-4">
              <section className="flex min-w-0 flex-[1.7] flex-col overflow-hidden rounded-lg border border-[#25293a] bg-[#12141c]">
                <div className="flex items-center justify-between border-b border-[#25293a] px-4 py-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-200">歌单顺序编排</h2>
                    <p className="mt-0.5 text-[11px] text-slate-500">拖动音轨或使用位移键；该顺序同时用于网易云与视频</p>
                  </div>
                  <span
                    className={`font-mono text-[10px] ${
                      saveState === 'error' ? 'text-rose-400' : saveState === 'saving' ? 'text-amber-400' : 'text-emerald-400'
                    }`}
                  >
                    {saveState === 'saving' ? '○ 正在同步排序…' : saveState === 'error' ? '⚠ 排序未同步' : '● 排序已保存'}
                  </span>
                </div>
                <div className="min-h-0 flex-1 overflow-auto">
                  <table className="w-full border-collapse text-left text-xs">
                    <thead className="sticky top-0 z-10 bg-[#181b26] text-[10px] uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="w-10 px-2 py-2">序</th>
                        <th className="px-2 py-2">歌曲</th>
                        <th className="px-3 py-2">艺人</th>
                        <th className="px-3 py-2">实际发行日</th>
                        <th className="w-28 px-2 py-2 text-right">位移</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#222634]">
                      {ordered.map((item, index) => (
                        <ReadyRow
                          key={`${item.candidate_id}-${item.netease_track_id}`}
                          item={item}
                          index={index}
                          total={ordered.length}
                          dragging={dragId === item.candidate_id}
                          onDrag={() => setDragId(item.candidate_id)}
                          onDrop={() => dropOn(index)}
                          onMove={to => move(index, to)}
                          onOpenMatch={() => setMatchingItem(item)}
                          onClearOverride={() => void handleClearOverride(item)}
                          onRemove={() => void handleExclude(item)}
                        />
                      ))}
                    </tbody>
                  </table>
                  {ordered.length === 0 && <Empty text="当前没有可发布歌曲" />}
                </div>
              </section>

              <section className="flex min-w-[330px] flex-1 flex-col gap-3 overflow-auto">
                <IssueBox
                  title="匹配存疑"
                  icon={<AlertTriangle className="h-4 w-4" />}
                  tone="amber"
                  items={preview.ambiguous_items}
                  onOpenMatch={item => setMatchingItem(item)}
                  onClearOverride={item => void handleClearOverride(item)}
                />
                <IssueBox
                  title="网易云未找到"
                  icon={<SearchX className="h-4 w-4" />}
                  tone="rose"
                  items={preview.unmatched_items}
                  onOpenMatch={item => setMatchingItem(item)}
                  onClearOverride={item => void handleClearOverride(item)}
                />
                <IssueBox
                  title="非本周发行 · 已隔离"
                  icon={<AlertTriangle className="h-4 w-4" />}
                  tone="violet"
                  items={preview.out_of_week_items}
                  onOpenMatch={item => setMatchingItem(item)}
                  onClearOverride={item => void handleClearOverride(item)}
                />
                {preview.excluded_items && preview.excluded_items.length > 0 && (
                  <ExcludedBox
                    items={preview.excluded_items}
                    onRestore={item => void handleRestore(item)}
                  />
                )}
              </section>
            </div>

            {videoJob && (
              <div className="flex min-h-9 items-center justify-between border-t border-cyan-900/50 bg-cyan-950/20 px-6 py-1 text-[11px]">
                <span className="font-mono text-cyan-300">
                  [视频流水线]{' '}
                  {videoJob.status === 'running'
                    ? '正在抓取、研究与渲染'
                    : videoJob.status === 'completed'
                    ? '已完成'
                    : videoJob.status === 'failed'
                    ? `失败：${videoJob.error}`
                    : videoJob.status === 'cancelled'
                    ? '已取消'
                    : '排队中'}
                </span>
                <div className="flex items-center gap-3">
                  {videoJob.artifacts?.video && (
                    <a
                      className="flex items-center gap-1 text-emerald-300 hover:text-emerald-200"
                      href={videoJob.artifacts.video}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ExternalLink className="h-3 w-3" />打开成片
                    </a>
                  )}
                  {videoJob.artifacts?.release_report && (
                    <a className="text-cyan-300 hover:text-cyan-200" href={videoJob.artifacts.release_report} target="_blank" rel="noreferrer">
                      周报
                    </a>
                  )}
                  {videoJob.artifacts?.publish_copy && (
                    <button
                      onClick={() => setShowCopyPanel(v => !v)}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition ${
                        showCopyPanel
                          ? 'bg-amber-400/20 text-amber-300 border border-amber-500/40'
                          : 'text-amber-300 hover:text-amber-200'
                      }`}
                    >
                      <FileText className="h-3 w-3" />发布文案
                    </button>
                  )}
                  <button
                    onClick={() => setShowTaskCenter(true)}
                    className="flex items-center gap-1 text-slate-400 hover:text-slate-200 transition"
                  >
                    <History className="h-3 w-3" />任务 {videoJob.job_id}
                  </button>
                </div>
              </div>
            )}

            {showCopyPanel && videoJob && (
              <PublishCopyPanel jobId={videoJob.job_id} onClose={() => setShowCopyPanel(false)} />
            )}

            <div className="flex items-center justify-between border-t border-[#232736] bg-[#12141c] px-6 py-3">
              <div className="min-w-0 text-xs">
                {error ? (
                  <span className="text-rose-400">{error}</span>
                ) : message ? (
                  <span className="text-emerald-400">{message}</span>
                ) : (
                  <span className="text-slate-500">
                    歌单名：<strong className="text-slate-300">{preview.playlist_name}</strong>
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  disabled={!!action}
                  onClick={() => void reload()}
                  className="flex items-center gap-1.5 rounded border border-[#33394d] bg-[#1b1e2a] px-3 py-2 text-xs text-slate-300 hover:bg-[#252a3a] disabled:opacity-50"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />重新匹配
                </button>
                <button
                  disabled={!!action}
                  onClick={() => void exportRelease()}
                  className="flex items-center gap-1.5 rounded border border-blue-700/60 bg-blue-950/30 px-3 py-2 text-xs font-medium text-blue-300 hover:bg-blue-950/60 disabled:opacity-50"
                >
                  {action === 'export' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                  导出物料包
                </button>
                <button
                  disabled={!!action || ordered.length === 0 || saveState === 'saving'}
                  onClick={() => void publish()}
                  title="按当前顺序生成网易云歌单，并自动启动视频工作流"
                  className="flex items-center gap-1.5 rounded bg-cyan-600 px-4 py-2 text-xs font-semibold text-white shadow hover:bg-cyan-500 disabled:opacity-40"
                >
                  {action === 'publish' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
                  {action === 'publish' ? '正在生成网易云歌单…' : `正式发布并启动视频 (${ordered.length}首)`}
                </button>
              </div>
            </div>
          </>
        )
      )}

      {/* Manual Match Search & Override Modal */}
      {matchingItem && (
        <ManualMatchModal
          item={matchingItem}
          onClose={() => setMatchingItem(null)}
          onSuccess={async (trackId, title) => {
            setMatchingItem(null);
            setMessage(`已为《${matchingItem.track_title}》绑定网易云曲目：《${title}》(ID: ${trackId})，已重新计算交付预演。`);
            await reload();
          }}
        />
      )}

      {/* Video Workflow Task Center Drawer / Modal */}
      {showTaskCenter && (
        <VideoTaskCenterModal
          onClose={() => setShowTaskCenter(false)}
          onResumed={async resumedJobId => {
            setMessage(`视频任务 ${resumedJobId} 恢复成功，已复用原有曲目数据重新启动`);
            await fetchLatestVideoWorkflow().then(x => setVideoJob(x.job || null)).catch(() => {});
          }}
        />
      )}
    </main>
  );
}

// ----------------------------------------------------------------------------
// Manual Match Modal
// ----------------------------------------------------------------------------
function ManualMatchModal({
  item,
  onClose,
  onSuccess,
}: {
  item: PublicationItem;
  onClose: () => void;
  onSuccess: (trackId: string, title: string) => void;
}) {
  const [keyword, setKeyword] = useState(`${item.track_title} ${item.artist_names}`.trim());
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<NetEaseSearchItem[]>([]);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [customId, setCustomId] = useState('');
  const [customTitle, setCustomTitle] = useState(item.track_title);
  const [customArtists, setCustomArtists] = useState(item.artist_names);
  const [customReleaseDate, setCustomReleaseDate] = useState(item.source_release_date || item.effective_release_date || '');
  const [customNotes, setCustomNotes] = useState('');

  const doSearch = useCallback(async (kw: string) => {
    if (!kw.trim()) return;
    setSearching(true);
    setError('');
    try {
      const res = await searchNetEaseSongs(kw.trim(), 12);
      setResults(res.results || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : '搜索失败');
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    void doSearch(keyword);
  }, []);

  const handleSelect = async (candidate: NetEaseSearchItem) => {
    setSavingId(candidate.id);
    setError('');
    try {
      await setManualMatch({
        candidate_id: item.candidate_id,
        netease_track_id: candidate.id,
        matched_title: candidate.title || item.track_title,
        matched_artists: candidate.artists || item.artist_names,
        target_release_date: candidate.release_date || item.source_release_date || '',
        notes: `通过搜索选择匹配 (${candidate.album || ''})`,
      });
      onSuccess(candidate.id, candidate.title);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存匹配失败');
      setSavingId(null);
    }
  };

  const handleDirectSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!customId.trim()) return;
    setSavingId('direct');
    setError('');
    try {
      await setManualMatch({
        candidate_id: item.candidate_id,
        netease_track_id: customId.trim(),
        matched_title: customTitle.trim() || item.track_title,
        matched_artists: customArtists.trim() || item.artist_names,
        target_release_date: customReleaseDate.trim(),
        notes: customNotes.trim() || '手动指定曲目ID',
      });
      onSuccess(customId.trim(), customTitle.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存匹配失败');
      setSavingId(null);
    }
  };

  const formatDuration = (ms: number) => {
    if (!ms) return '--:--';
    const totalSec = Math.floor(ms / 1000);
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    return `${min}:${sec.toString().padStart(2, '0')}`;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl border border-[#2d3348] bg-[#12141d] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#232736] bg-[#161925] px-6 py-4">
          <div>
            <div className="flex items-center gap-2">
              <Search className="h-5 w-5 text-cyan-400" />
              <h2 className="text-sm font-semibold text-slate-100">人工网易云曲目匹配</h2>
              <span className="rounded bg-violet-950/60 border border-violet-700/60 px-2 py-0.5 text-[10px] font-mono text-violet-300 uppercase">
                {item.platform}
              </span>
              {item.is_manual_override && (
                <span className="rounded bg-emerald-950/60 border border-emerald-700/60 px-2 py-0.5 text-[10px] font-mono text-emerald-300">
                  当前已有人工匹配
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-slate-400">
              目标曲目：<strong className="text-slate-200">《{item.track_title}》</strong> - {item.artist_names}
              {item.source_release_date && <span className="ml-2 font-mono text-slate-500">(源发行: {item.source_release_date})</span>}
            </p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-[#252a3a] hover:text-slate-200">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-6">
          {error && <div className="rounded border border-rose-800/60 bg-rose-950/30 p-2.5 text-xs text-rose-300">{error}</div>}

          {/* Search bar */}
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                type="text"
                value={keyword}
                onChange={e => setKeyword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && void doSearch(keyword)}
                placeholder="输入歌名、艺人、网易云歌曲链接或数字 ID 搜索…"
                className="w-full rounded border border-[#2a2f42] bg-[#0b0c13] px-3.5 py-2 text-xs text-slate-200 placeholder-slate-500 focus:border-cyan-500 focus:outline-none"
              />
            </div>
            <button
              onClick={() => void doSearch(keyword)}
              disabled={searching}
              className="flex items-center gap-1.5 rounded bg-cyan-600 px-4 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:opacity-50"
            >
              {searching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
              搜索网易云
            </button>
          </div>

          {/* Search Results List */}
          <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-[#232736] bg-[#0c0d14] overflow-hidden">
            <div className="flex items-center justify-between border-b border-[#232736] bg-[#151722] px-3 py-2 text-[11px] font-medium text-slate-400">
              <span>搜索候选列表 ({results.length})</span>
              <span className="text-[10px] text-slate-500">点击「选择匹配」立即生效并更新交付单</span>
            </div>
            <div className="min-h-0 flex-1 divide-y divide-[#1e2230] overflow-auto">
              {searching ? (
                <div className="flex h-36 items-center justify-center gap-2 text-xs text-cyan-300/80">
                  <Loader2 className="h-4 w-4 animate-spin" />正在搜索网易云曲库…
                </div>
              ) : results.length === 0 ? (
                <div className="flex h-32 flex-col items-center justify-center gap-1 text-xs text-slate-500">
                  <SearchX className="h-5 w-5 text-slate-600" />
                  <span>未检索到匹配结果，请调整关键词或在下方直接粘贴歌曲 ID / 链接</span>
                </div>
              ) : (
                results.map(cand => (
                  <div key={cand.id} className="flex items-center justify-between px-3.5 py-2.5 hover:bg-[#141724] transition">
                    <div className="min-w-0 flex-1 pr-4">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-xs font-medium text-slate-200">{cand.title}</span>
                        <span className="font-mono text-[10px] text-slate-500">ID: {cand.id}</span>
                        {cand.duration_ms > 0 && (
                          <span className="font-mono text-[10px] text-slate-400">[{formatDuration(cand.duration_ms)}]</span>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-3 text-[11px] text-slate-400">
                        <span className="truncate max-w-[200px]">{cand.artists || '未知艺人'}</span>
                        {cand.album && <span className="truncate max-w-[180px] text-slate-500">《{cand.album}》</span>}
                        {cand.release_date && <span className="font-mono text-[10px] text-emerald-400">{cand.release_date}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <a
                        href={cand.url}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1 text-slate-500 hover:text-cyan-300"
                        title="在网易云网页打开"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                      <button
                        onClick={() => void handleSelect(cand)}
                        disabled={savingId === cand.id}
                        className="flex items-center gap-1 rounded bg-cyan-700 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-cyan-600 disabled:opacity-50"
                      >
                        {savingId === cand.id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                        选择匹配
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Direct Manual Input Section */}
          <form onSubmit={handleDirectSave} className="rounded-lg border border-[#232736] bg-[#141722] p-3 text-xs">
            <div className="mb-2 font-semibold text-slate-300 flex items-center gap-1.5">
              <LinkIcon className="h-3.5 w-3.5 text-amber-400" />
              <span>直接指定网易云歌曲 ID 或链接 (精准覆盖)</span>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <input
                type="text"
                value={customId}
                onChange={e => setCustomId(e.target.value)}
                placeholder="网易云歌曲 ID 或 URL (如 18332095)"
                className="rounded border border-[#2a2f42] bg-[#0b0c13] px-2.5 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:border-cyan-500 focus:outline-none"
              />
              <input
                type="text"
                value={customReleaseDate}
                onChange={e => setCustomReleaseDate(e.target.value)}
                placeholder="发行日期 YYYY-MM-DD (可选)"
                className="rounded border border-[#2a2f42] bg-[#0b0c13] px-2.5 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:border-cyan-500 focus:outline-none"
              />
              <div className="flex gap-2">
                <input
                  type="text"
                  value={customNotes}
                  onChange={e => setCustomNotes(e.target.value)}
                  placeholder="备注 (如 人工确认)"
                  className="min-w-0 flex-1 rounded border border-[#2a2f42] bg-[#0b0c13] px-2.5 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:border-cyan-500 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!customId.trim() || savingId === 'direct'}
                  className="rounded bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-500 disabled:opacity-40"
                >
                  {savingId === 'direct' ? <Loader2 className="h-3 w-3 animate-spin" /> : '指定并保存'}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Video Task Center Modal
// ----------------------------------------------------------------------------
function VideoTaskCenterModal({
  onClose,
  onResumed,
}: {
  onClose: () => void;
  onResumed: (jobId: string) => void;
}) {
  const [jobs, setJobs] = useState<VideoWorkflowJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [resumingJobId, setResumingJobId] = useState<string | null>(null);
  const [viewingLogJobId, setViewingLogJobId] = useState<string | null>(null);
  const [error, setError] = useState('');

  const loadJobs = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const list = await fetchVideoWorkflowJobs();
      setJobs(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载视频任务列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadJobs();
    const timer = window.setInterval(loadJobs, 4000);
    return () => window.clearInterval(timer);
  }, [loadJobs]);

  const handleResume = async (job: VideoWorkflowJob) => {
    const trackCount = job.track_count || (job.ordered_track_ids ? job.ordered_track_ids.length : 0);
    const confirmPrompt =
      `确认恢复视频任务【${job.job_id}】？\n\n` +
      `• 将复用该任务已有的 ${trackCount} 首曲目数据与已有分析产物重新启动视频流水线\n` +
      `• 禁止重新创建或发布网易云歌单，确保原曲目 ID 与歌单一致\n` +
      `• 状态将恢复为运行中`;
    if (!window.confirm(confirmPrompt)) return;

    setResumingJobId(job.job_id);
    setError('');
    try {
      await resumeVideoWorkflowJob(job.job_id);
      onResumed(job.job_id);
      await loadJobs();
    } catch (err) {
      setError(err instanceof Error ? err.message : '恢复任务失败');
    } finally {
      setResumingJobId(null);
    }
  };

  const getStatusBadge = (status: VideoWorkflowJob['status']) => {
    switch (status) {
      case 'completed':
        return <span className="rounded bg-emerald-950/60 border border-emerald-700/60 px-2 py-0.5 text-[10px] font-mono text-emerald-300">已完成</span>;
      case 'running':
        return <span className="rounded bg-cyan-950/60 border border-cyan-700/60 px-2 py-0.5 text-[10px] font-mono text-cyan-300 animate-pulse">运行中</span>;
      case 'queued':
        return <span className="rounded bg-amber-950/60 border border-amber-700/60 px-2 py-0.5 text-[10px] font-mono text-amber-300">排队中</span>;
      case 'failed':
        return <span className="rounded bg-rose-950/60 border border-rose-700/60 px-2 py-0.5 text-[10px] font-mono text-rose-300">失败</span>;
      case 'cancelled':
        return <span className="rounded bg-slate-800 border border-slate-700 px-2 py-0.5 text-[10px] font-mono text-slate-400">已取消</span>;
      default:
        return <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] font-mono text-slate-400">{status}</span>;
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl border border-[#2d3348] bg-[#12141d] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#232736] bg-[#161925] px-6 py-4">
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-cyan-400" />
            <h2 className="text-sm font-semibold text-slate-100">视频任务中心 (Task Center)</h2>
            <span className="rounded border border-cyan-700/60 bg-cyan-950/40 px-2 py-0.5 font-mono text-[10px] text-cyan-300">
              {jobs.length} TASKS
            </span>
          </div>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-[#252a3a] hover:text-slate-200">
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-auto p-6">
          {error && <div className="rounded border border-rose-800/60 bg-rose-950/30 p-2.5 text-xs text-rose-300">{error}</div>}

          <div className="min-h-0 flex-1 rounded-lg border border-[#232736] bg-[#0c0d14] overflow-hidden">
            <table className="w-full border-collapse text-left text-xs">
              <thead className="sticky top-0 z-10 bg-[#161926] text-[10px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-3 py-2.5">任务 ID / 创建时间</th>
                  <th className="px-3 py-2.5">状态 / 阶段</th>
                  <th className="px-3 py-2.5">曲目数</th>
                  <th className="px-3 py-2.5">产物与日志</th>
                  <th className="px-3 py-2.5 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e2230]">
                {loading && jobs.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin text-cyan-400 mb-1" />
                      加载任务中…
                    </td>
                  </tr>
                ) : jobs.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                      暂无视频任务记录
                    </td>
                  </tr>
                ) : (
                  jobs.map(job => {
                    const isResumable = job.status === 'failed' || job.status === 'cancelled';
                    const isRunning = job.status === 'running' || job.status === 'queued';
                    const trackCount = job.track_count || (job.ordered_track_ids ? job.ordered_track_ids.length : 0);

                    return (
                      <tr key={job.job_id} className="hover:bg-[#131622] transition">
                        <td className="px-3 py-2.5">
                          <div className="font-mono text-xs font-semibold text-slate-200">{job.job_id}</div>
                          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-slate-500 font-mono">
                            <span>{job.created_at.replace('T', ' ').replace('Z', '')}</span>
                            {job.playlist_url && (
                              <a
                                href={job.playlist_url}
                                target="_blank"
                                rel="noreferrer"
                                className="text-cyan-400 hover:underline flex items-center gap-0.5"
                              >
                                歌单 <ExternalLink className="h-2.5 w-2.5" />
                              </a>
                            )}
                          </div>
                          {job.error && (
                            <div className="mt-1 max-w-sm rounded bg-rose-950/40 border border-rose-800/40 p-1 font-mono text-[10px] text-rose-300 truncate">
                              错误: {job.error}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          {getStatusBadge(job.status)}
                          <div className="mt-1 font-mono text-[10px] text-cyan-400/80">{job.stage_label || '状态识别中'}</div>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-slate-300">{trackCount} 首</td>
                        <td className="px-3 py-2.5">
                          <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                            {job.artifacts?.video && (
                              <a
                                href={job.artifacts.video}
                                target="_blank"
                                rel="noreferrer"
                                className="rounded bg-emerald-950/40 border border-emerald-700/50 px-1.5 py-0.5 text-emerald-300 hover:bg-emerald-900/60"
                              >
                                成片
                              </a>
                            )}
                            {job.artifacts?.release_report && (
                              <a
                                href={job.artifacts.release_report}
                                target="_blank"
                                rel="noreferrer"
                                className="rounded bg-cyan-950/40 border border-cyan-700/50 px-1.5 py-0.5 text-cyan-300 hover:bg-cyan-900/60"
                              >
                                周报
                              </a>
                            )}
                            {job.artifacts?.publish_copy && (
                              <a
                                href={job.artifacts.publish_copy}
                                target="_blank"
                                rel="noreferrer"
                                className="rounded bg-amber-950/40 border border-amber-700/50 px-1.5 py-0.5 text-amber-300 hover:bg-amber-900/60"
                              >
                                文案
                              </a>
                            )}
                            <button
                              onClick={() => setViewingLogJobId(job.job_id)}
                              className="rounded bg-[#1b1e2a] border border-[#33394d] px-1.5 py-0.5 text-slate-300 hover:bg-[#252a3a] hover:text-cyan-300 flex items-center gap-0.5"
                              title="查看实时/归档流水线日志"
                            >
                              <FileText className="h-2.5 w-2.5" />
                              日志
                            </button>
                            {!job.artifacts || Object.keys(job.artifacts).length === 0 ? (
                              <span className="text-slate-600">--</span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 text-right">
                          {isResumable ? (
                            <button
                              onClick={() => void handleResume(job)}
                              disabled={resumingJobId === job.job_id}
                              className="inline-flex items-center gap-1 rounded border border-amber-700/60 bg-amber-950/40 px-2.5 py-1 text-[11px] font-medium text-amber-300 hover:bg-amber-900/60 disabled:opacity-50"
                              title="复用原曲目数据与已有分析产物重新启动流水线"
                            >
                              {resumingJobId === job.job_id ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <RotateCcw className="h-3 w-3" />
                              )}
                              恢复任务
                            </button>
                          ) : isRunning ? (
                            <span className="text-[11px] text-cyan-400 font-mono">运行中</span>
                          ) : (
                            <span className="text-[11px] text-slate-500 font-mono">已归档</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {viewingLogJobId && (
        <WorkflowLogModal jobId={viewingLogJobId} onClose={() => setViewingLogJobId(null)} />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Workflow Log Viewer Modal
// ----------------------------------------------------------------------------
function WorkflowLogModal({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [logText, setLogText] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const fetchLog = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const resp = await fetch(`/api/video-workflow/${encodeURIComponent(jobId)}/artifacts/log`);
      if (!resp.ok) {
        throw new Error(`日志读取失败 (${resp.status}): ${resp.statusText}`);
      }
      const text = await resp.text();
      setLogText(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载日志失败');
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void fetchLog();
  }, [fetchLog]);

  const handleCopy = async () => {
    if (!logText) return;
    try {
      await navigator.clipboard.writeText(logText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl border border-[#2d3348] bg-[#0c0e15] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#232736] bg-[#141622] px-6 py-3.5">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-cyan-400" />
            <h3 className="text-sm font-semibold text-slate-200">执行日志：{jobId}</h3>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void fetchLog()}
              disabled={loading}
              className="flex items-center gap-1 rounded border border-[#33394d] bg-[#1b1e2a] px-2.5 py-1 text-[11px] text-slate-300 hover:bg-[#252a3a] disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
            <button
              onClick={handleCopy}
              disabled={!logText}
              className="flex items-center gap-1 rounded border border-[#33394d] bg-[#1b1e2a] px-2.5 py-1 text-[11px] text-slate-300 hover:bg-[#252a3a] disabled:opacity-50"
            >
              {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
              {copied ? '已复制' : '复制日志'}
            </button>
            <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-[#252a3a] hover:text-slate-200">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        {/* Terminal Content */}
        <div className="min-h-0 flex-1 overflow-auto bg-[#07080d] p-4 font-mono text-[11px] leading-relaxed text-slate-300 whitespace-pre-wrap">
          {loading ? (
            <div className="flex h-48 items-center justify-center gap-2 text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-cyan-400" />
              正在读取 workflow.log...
            </div>
          ) : error ? (
            <div className="rounded border border-rose-900/60 bg-rose-950/30 p-3 text-rose-300">{error}</div>
          ) : logText ? (
            logText
          ) : (
            <span className="text-slate-600">日志为空</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Publish Copy Panel Component
// ----------------------------------------------------------------------------
function PublishCopyPanel({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const [data, setData] = useState<PublishCopyData | null>(null);
  const [text, setText] = useState('');
  const [savedText, setSavedText] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setFeedback(null);
    try {
      const res = await fetchPublishCopy(jobId);
      setData(res);
      setText(res.editable_text);
      setSavedText(res.editable_text);
    } catch (err) {
      setFeedback({ type: 'error', msg: err instanceof Error ? err.message : '加载文案失败' });
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  const isDirty = text !== savedText;

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      setFeedback({ type: 'success', msg: '文案已复制到剪贴板' });
    } catch {
      setFeedback({ type: 'error', msg: '复制失败，请手动选中复制' });
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const res = await savePublishCopy(jobId, text);
      setSavedText(text);
      setData(prev => (prev ? { ...prev, editable_text: text, is_modified: res.is_modified } : null));
      setFeedback({ type: 'success', msg: '修改已保存' });
    } catch (err) {
      setFeedback({ type: 'error', msg: err instanceof Error ? err.message : '保存失败' });
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (isDirty || (data && data.is_modified)) {
      if (!window.confirm('确认放弃当前修改并恢复为自动生成的文案？')) return;
    }
    setResetting(true);
    setFeedback(null);
    try {
      const res = await resetPublishCopy(jobId);
      setText(res.text);
      setSavedText(res.text);
      setData(prev => (prev ? { ...prev, editable_text: res.text, is_modified: false } : null));
      setFeedback({ type: 'success', msg: '已恢复为自动版本' });
    } catch (err) {
      setFeedback({ type: 'error', msg: err instanceof Error ? err.message : '恢复失败' });
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="border-t border-[#232736] bg-[#10121a] px-6 py-3">
      <div className="flex items-center justify-between pb-2">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-amber-400" />
          <span className="text-xs font-semibold text-slate-200">发布文案编辑</span>
          {isDirty ? (
            <span className="rounded bg-amber-950/60 border border-amber-700/60 px-1.5 py-0.2 text-[10px] text-amber-300 font-mono">
              ● 未保存更改
            </span>
          ) : data?.is_modified ? (
            <span className="rounded bg-violet-950/60 border border-violet-700/60 px-1.5 py-0.2 text-[10px] text-violet-300 font-mono">
              已自定义
            </span>
          ) : (
            <span className="rounded bg-emerald-950/60 border border-emerald-700/60 px-1.5 py-0.2 text-[10px] text-emerald-300 font-mono">
              自动版本
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {feedback && (
            <span className={`text-[11px] font-mono ${feedback.type === 'success' ? 'text-emerald-400' : 'text-rose-400'}`}>
              {feedback.msg}
            </span>
          )}
          <button
            onClick={handleCopy}
            disabled={loading || !text}
            title="复制当前文案到剪贴板"
            className="flex items-center gap-1 rounded border border-[#33394d] bg-[#1b1e2a] px-2.5 py-1 text-[11px] text-slate-300 hover:bg-[#252a3a] disabled:opacity-50"
          >
            {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
            {copied ? '已复制' : '复制全文'}
          </button>
          <button
            onClick={handleReset}
            disabled={loading || resetting}
            title="重置为初始自动生成的底稿"
            className="flex items-center gap-1 rounded border border-amber-800/60 bg-amber-950/30 px-2.5 py-1 text-[11px] text-amber-300 hover:bg-amber-950/60 disabled:opacity-50"
          >
            {resetting ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
            恢复自动版本
          </button>
          <button
            onClick={handleSave}
            disabled={loading || saving || !isDirty}
            title="保存修改到文案文件"
            className="flex items-center gap-1 rounded bg-amber-600 px-3 py-1 text-[11px] font-medium text-white hover:bg-amber-500 disabled:opacity-40"
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            保存修改
          </button>
          <button onClick={onClose} className="p-1 text-slate-500 hover:text-slate-300" title="关闭面板">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {loading ? (
        <div className="flex h-36 items-center justify-center gap-2 text-xs text-amber-300/80">
          <Loader2 className="h-4 w-4 animate-spin" />正在载入发布文案…
        </div>
      ) : (
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={7}
          className="w-full rounded border border-[#262a3c] bg-[#0c0d14] p-2.5 font-mono text-xs text-slate-200 placeholder-slate-600 focus:border-amber-500/60 focus:outline-none leading-relaxed"
          placeholder="发布文案内容..."
        />
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone: 'emerald' | 'amber' | 'rose' | 'violet';
}) {
  const colors = {
    emerald: 'border-emerald-800/60 bg-emerald-950/20 text-emerald-400',
    amber: 'border-amber-800/60 bg-amber-950/20 text-amber-400',
    rose: 'border-rose-900/60 bg-rose-950/20 text-rose-400',
    violet: 'border-violet-800/60 bg-violet-950/20 text-violet-400',
  };
  return (
    <div className={`rounded-lg border p-3 ${colors[tone]}`}>
      <div className="text-[10px] uppercase tracking-widest opacity-75">{label}</div>
      <div className="mt-1 text-2xl font-semibold font-mono">{value}</div>
      <div className="mt-1 text-[10px] text-slate-500">{note}</div>
    </div>
  );
}

function ReadyRow({
  item,
  index,
  total,
  dragging,
  onDrag,
  onDrop,
  onMove,
  onOpenMatch,
  onClearOverride,
  onRemove,
}: {
  item: PublicationItem;
  index: number;
  total: number;
  dragging: boolean;
  onDrag: () => void;
  onDrop: () => void;
  onMove: (to: number) => void;
  onOpenMatch: () => void;
  onClearOverride: () => void;
  onRemove: () => void;
}) {
  return (
    <tr
      draggable
      onDragStart={onDrag}
      onDragOver={e => e.preventDefault()}
      onDrop={onDrop}
      className={`${dragging ? 'opacity-40' : 'opacity-100'} group hover:bg-[#1b1e29]`}
    >
      <td className="px-2 py-2 font-mono text-slate-600">
        <span className="flex items-center gap-1">
          <GripVertical className="h-3.5 w-3.5 cursor-grab text-slate-600 group-hover:text-cyan-400" />
          {String(index + 1).padStart(2, '0')}
        </span>
      </td>
      <td className="max-w-[240px] px-2 py-2">
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium text-slate-200">{item.track_title}</span>
          {item.is_manual_override && (
            <span className="rounded bg-emerald-950/70 border border-emerald-700/60 px-1 py-0.2 text-[9px] font-mono text-emerald-300 shrink-0">
              人工
            </span>
          )}
        </div>
        {item.matched_title && item.matched_title !== item.track_title && (
          <div className="truncate text-[9px] text-slate-500">匹配为 《{item.matched_title}》</div>
        )}
      </td>
      <td className="max-w-[150px] truncate px-3 py-2 text-slate-400">
        <ArtistHoverTrigger artistName={item.artist_names} songTitle={item.track_title}>
          <span className="cursor-help hover:text-slate-200 transition">{item.artist_names}</span>
        </ArtistHoverTrigger>
      </td>
      <td className="px-3 py-2">
        <div className="font-mono text-[9px] text-emerald-400">{item.effective_release_date}</div>
        <div className="text-[8px] text-slate-600">源 {item.source_release_date || '--'}</div>
      </td>
      <td className="px-2 py-2">
        <div className="flex justify-end items-center gap-0.5 opacity-20 group-hover:opacity-100">
          <button
            onClick={onOpenMatch}
            title="更换/修改网易云匹配"
            className="flex h-5 w-5 items-center justify-center rounded bg-[#292d3a] text-slate-400 hover:bg-cyan-900 hover:text-cyan-300"
          >
            <Search className="h-3 w-3" />
          </button>
          {item.is_manual_override && (
            <button
              onClick={onClearOverride}
              title="清除人工匹配，恢复自动判断"
              className="flex h-5 w-5 items-center justify-center rounded bg-[#292d3a] text-amber-400 hover:bg-amber-950 hover:text-amber-200"
            >
              <RotateCcw className="h-3 w-3" />
            </button>
          )}
          {item.is_published ? (
            <button
              disabled
              title="该歌曲已发布至歌单，不可直接移除"
              className="flex h-5 w-5 items-center justify-center rounded bg-[#292d3a] text-slate-600 cursor-not-allowed opacity-40"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          ) : (
            <button
              onClick={onRemove}
              title="从本周交付中排除此曲（保留初筛与候选数据）"
              className="flex h-5 w-5 items-center justify-center rounded bg-[#292d3a] text-rose-400 hover:bg-rose-950 hover:text-rose-200"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          )}
          <TinyMove title="置顶" disabled={index === 0} onClick={() => onMove(0)} icon={<ChevronsUp />} />
          <TinyMove title="上移" disabled={index === 0} onClick={() => onMove(index - 1)} icon={<MoveUp />} />
          <TinyMove title="下移" disabled={index === total - 1} onClick={() => onMove(index + 1)} icon={<MoveDown />} />
          <TinyMove title="置底" disabled={index === total - 1} onClick={() => onMove(total - 1)} icon={<ChevronsDown />} />
        </div>
      </td>
    </tr>
  );
}

function TinyMove({
  title,
  disabled,
  onClick,
  icon,
}: {
  title: string;
  disabled: boolean;
  onClick: () => void;
  icon: React.ReactElement;
}) {
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={onClick}
      className="flex h-5 w-5 items-center justify-center rounded bg-[#292d3a] text-slate-400 hover:bg-cyan-900 hover:text-cyan-300 disabled:opacity-20"
    >
      {React.cloneElement(icon, { className: 'h-3 w-3' } as React.HTMLAttributes<SVGElement>)}
    </button>
  );
}

function IssueBox({
  title,
  icon,
  tone,
  items,
  onOpenMatch,
  onClearOverride,
}: {
  title: string;
  icon: React.ReactNode;
  tone: 'amber' | 'rose' | 'violet';
  items: PublicationItem[];
  onOpenMatch: (item: PublicationItem) => void;
  onClearOverride: (item: PublicationItem) => void;
}) {
  const color =
    tone === 'amber'
      ? 'text-amber-400 border-amber-900/50'
      : tone === 'violet'
      ? 'text-violet-400 border-violet-900/50'
      : 'text-rose-400 border-rose-900/50';

  return (
    <div className={`rounded-lg border bg-[#12141c] ${color}`}>
      <div className="flex items-center justify-between border-b border-inherit px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          {icon}
          {title}
        </div>
        <span className="font-mono text-xs">{items.length}</span>
      </div>
      <div className="max-h-48 divide-y divide-[#222634] overflow-auto">
        {items.map(item => (
          <div key={`${title}-${item.candidate_id}`} className="group px-3 py-2 hover:bg-[#181b26] transition">
            <div className="flex items-center justify-between">
              <div className="truncate text-xs text-slate-200 flex items-center gap-1.5">
                <span className="truncate">{item.track_title}</span>
                {item.is_manual_override && (
                  <span className="rounded bg-emerald-950/70 border border-emerald-700/60 px-1 py-0.2 text-[9px] font-mono text-emerald-300 shrink-0">
                    人工
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1 opacity-80 group-hover:opacity-100">
                <button
                  onClick={() => onOpenMatch(item)}
                  className="rounded bg-cyan-950/70 border border-cyan-800/60 px-1.5 py-0.5 text-[10px] font-medium text-cyan-300 hover:bg-cyan-900"
                  title="手动搜索并匹配网易云曲目"
                >
                  手动匹配
                </button>
                {item.is_manual_override && (
                  <button
                    onClick={() => onClearOverride(item)}
                    className="p-1 text-slate-500 hover:text-amber-400"
                    title="清除人工匹配，恢复自动判断"
                  >
                    <RotateCcw className="h-3 w-3" />
                  </button>
                )}
              </div>
            </div>
            <div className="mt-0.5 flex items-center justify-between text-[10px] text-slate-500">
              <ArtistHoverTrigger artistName={item.artist_names} songTitle={item.track_title}>
                <span className="max-w-[180px] truncate cursor-help hover:text-slate-300">{item.artist_names}</span>
              </ArtistHoverTrigger>
              <span className="font-mono text-amber-500">{item.effective_release_date || item.release_gate_reason}</span>
            </div>
          </div>
        ))}
        {items.length === 0 && (
          <div className="flex items-center gap-1.5 px-3 py-4 text-[11px] text-slate-600">
            <CheckCircle2 className="h-3.5 w-3.5" />没有待处理项目
          </div>
        )}
      </div>
    </div>
  );
}

function ExcludedBox({
  items,
  onRestore,
}: {
  items: PublicationItem[];
  onRestore: (item: PublicationItem) => void;
}) {
  return (
    <div className="rounded-lg border border-slate-800/80 bg-[#12141c] text-slate-400">
      <div className="flex items-center justify-between border-b border-slate-800/80 px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-300">
          <Trash2 className="h-4 w-4 text-slate-500" />
          本期已移除排除 ({items.length})
        </div>
      </div>
      <div className="max-h-48 divide-y divide-[#222634] overflow-auto">
        {items.map(item => (
          <div key={`excluded-${item.candidate_id}`} className="group px-3 py-2 hover:bg-[#181b26] transition">
            <div className="flex items-center justify-between">
              <div className="truncate text-xs text-slate-300">
                <span className="line-through text-slate-500 mr-1.5">{item.track_title}</span>
              </div>
              <button
                onClick={() => onRestore(item)}
                className="rounded bg-violet-950/70 border border-violet-800/60 px-1.5 py-0.5 text-[10px] font-medium text-violet-300 hover:bg-violet-900 transition"
                title="恢复至本期交付列表"
              >
                恢复
              </button>
            </div>
            <div className="mt-0.5 flex items-center justify-between text-[10px] text-slate-500">
              <ArtistHoverTrigger artistName={item.artist_names} songTitle={item.track_title}>
                <span className="max-w-[180px] truncate cursor-help hover:text-slate-300">{item.artist_names}</span>
              </ArtistHoverTrigger>
              <span className="text-[9px] text-slate-600">已从交付排除</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="flex h-36 items-center justify-center text-xs text-slate-600">{text}</div>;
}

function Failure({ text, retry }: { text: string; retry: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center text-sm text-rose-300">
      <p>{text}</p>
      <button onClick={retry} className="mt-3 rounded bg-rose-800 px-3 py-1.5 text-xs text-white">
        重试
      </button>
    </div>
  );
}
