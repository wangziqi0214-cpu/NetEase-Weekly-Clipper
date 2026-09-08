import { CandidatePage, DatabaseStats, CandidateQueryParams, ReviewStatus, PublicationPreview, LearningSafety, VideoWorkflowJob, PublishCopyData } from './types';

const API_BASE = '/api';

export async function fetchCandidates(params: CandidateQueryParams): Promise<CandidatePage> {
  const search = new URLSearchParams();
  if (params.platform && params.platform !== 'all') search.set('platform', params.platform);
  if (params.status && params.status !== 'all') search.set('status', params.status);
  if (params.tier && params.tier !== 'all') search.set('tier', params.tier);
  if (params.keyword && params.keyword.trim()) search.set('keyword', params.keyword.trim());
  if (params.dedup !== undefined) search.set('dedup', String(params.dedup));

  const resp = await fetch(`${API_BASE}/candidates?${search.toString()}`);
  if (!resp.ok) throw new Error(`Failed to fetch candidates: ${resp.statusText}`);
  return (await resp.json()) as CandidatePage;
}

export async function fetchStats(): Promise<DatabaseStats> {
  const resp = await fetch(`${API_BASE}/stats`);
  if (!resp.ok) throw new Error(`Failed to fetch stats: ${resp.statusText}`);
  return (await resp.json()) as DatabaseStats;
}

export async function updateCandidateBatchStatus(
  rawIds: number[],
  status: ReviewStatus,
  notes?: string
): Promise<{ updated: number; success: boolean }> {
  const resp = await fetch(`${API_BASE}/candidates/batch-status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate_ids: rawIds, status, notes: notes || '' }),
  });
  if (!resp.ok) throw new Error(`Failed to update candidate status: ${resp.statusText}`);
  return (await resp.json()) as { updated: number; success: boolean };
}

export async function fetchPublicationPreview(): Promise<PublicationPreview> {
  const resp = await fetch(`${API_BASE}/publication/preview`);
  if (!resp.ok) throw new Error(`发布预演失败: ${resp.statusText}`);
  return (await resp.json()) as PublicationPreview;
}

export async function exportWeeklyRelease(): Promise<{ success: boolean; path: string; approved_count: number }> {
  const resp = await fetch(`${API_BASE}/publication/export`, { method: 'POST' });
  if (!resp.ok) throw new Error(`物料导出失败: ${resp.statusText}`);
  return await resp.json();
}

export async function savePublicationOrder(candidateIds: number[]) {
  const resp = await fetch(`${API_BASE}/publication/order`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_ids:candidateIds})});
  if(!resp.ok) throw new Error(`排序保存失败: ${resp.statusText}`);
  return await resp.json();
}

export async function fetchLatestVideoWorkflow() {
  const resp=await fetch(`${API_BASE}/video-workflow/latest`); if(!resp.ok) throw new Error('视频任务状态加载失败'); return await resp.json();
}

export async function fetchPublishCopy(jobId: string): Promise<PublishCopyData> {
  const resp = await fetch(`${API_BASE}/video-workflow/${encodeURIComponent(jobId)}/publish-copy`);
  if (!resp.ok) throw new Error(`发布文案加载失败: ${resp.statusText}`);
  return (await resp.json()) as PublishCopyData;
}

export async function savePublishCopy(jobId: string, text: string): Promise<{ success: boolean; job_id: string; text: string; is_modified: boolean }> {
  const resp = await fetch(`${API_BASE}/video-workflow/${encodeURIComponent(jobId)}/publish-copy`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) throw new Error(`发布文案保存失败: ${resp.statusText}`);
  return await resp.json();
}

export async function resetPublishCopy(jobId: string): Promise<{ success: boolean; job_id: string; text: string; is_modified: boolean }> {
  const resp = await fetch(`${API_BASE}/video-workflow/${encodeURIComponent(jobId)}/publish-copy/reset`, {
    method: 'POST',
  });
  if (!resp.ok) throw new Error(`文案恢复失败: ${resp.statusText}`);
  return await resp.json();
}

export async function publishWeeklyRelease(playlistName: string, allowPartial: boolean, orderedCandidateIds: number[]): Promise<{ video_workflow?: VideoWorkflowJob; playlist_url?: string }> {
  const resp = await fetch(`${API_BASE}/publication/publish`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ playlist_name: playlistName, allow_partial: allowPartial, confirm: true, ordered_candidate_ids: orderedCandidateIds, start_video_workflow: true }),
  });
  const raw = await resp.text();
  let payload: Record<string, unknown> = {};
  try { payload = raw ? JSON.parse(raw) as Record<string, unknown> : {}; } catch { /* keep the raw server message below */ }
  if (!resp.ok) throw new Error((typeof payload.detail === 'string' && payload.detail) || raw || `发布失败: ${resp.statusText}`);
  return payload as { video_workflow?: VideoWorkflowJob; playlist_url?: string };
}

export async function searchNetEaseSongs(keyword: string, limit: number = 10): Promise<{ count: number; results: import('./types').NetEaseSearchItem[] }> {
  const resp = await fetch(`${API_BASE}/netease/search?keyword=${encodeURIComponent(keyword)}&limit=${limit}`);
  if (!resp.ok) throw new Error(`网易云搜索失败: ${resp.statusText}`);
  return await resp.json();
}

export async function setManualMatch(payload: import('./types').ManualMatchPayload): Promise<{ success: boolean; candidate_id: number; netease_track_id: string }> {
  const resp = await fetch(`${API_BASE}/publication/manual-match`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `保存人工匹配失败: ${resp.statusText}`);
  return data;
}

export async function deleteManualMatch(candidateId: number): Promise<{ success: boolean; candidate_id: number }> {
  const resp = await fetch(`${API_BASE}/publication/manual-match/${candidateId}`, {
    method: 'DELETE',
  });
  if (!resp.ok) throw new Error(`清除人工匹配失败: ${resp.statusText}`);
  return await resp.json();
}

export async function fetchVideoWorkflowJobs(): Promise<import('./types').VideoWorkflowJob[]> {
  const resp = await fetch(`${API_BASE}/video-workflow/jobs`);
  if (!resp.ok) throw new Error(`视频任务列表加载失败: ${resp.statusText}`);
  const data = await resp.json();
  return data.jobs || [];
}

export async function fetchVideoWorkflowJob(jobId: string): Promise<import('./types').VideoWorkflowJob> {
  const resp = await fetch(`${API_BASE}/video-workflow/${encodeURIComponent(jobId)}`);
  if (!resp.ok) throw new Error(`视频任务详情加载失败: ${resp.statusText}`);
  const data = await resp.json();
  return data.job;
}

export async function resumeVideoWorkflowJob(jobId: string): Promise<{ success: boolean; job: import('./types').VideoWorkflowJob }> {
  const resp = await fetch(`${API_BASE}/video-workflow/${encodeURIComponent(jobId)}/resume`, {
    method: 'POST',
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `恢复任务失败: ${resp.statusText}`);
  return data;
}

export async function fetchLearningSafety(): Promise<LearningSafety> {
  const resp = await fetch(`${API_BASE}/learning/safety`);
  if (!resp.ok) throw new Error(`安全门数据加载失败: ${resp.statusText}`);
  return (await resp.json()) as LearningSafety;
}

export async function excludeDeliverySong(candidateId: number, playlistName?: string): Promise<{ success: boolean }> {
  const query = playlistName ? `?playlist_name=${encodeURIComponent(playlistName)}` : '';
  const resp = await fetch(`${API_BASE}/publication/delivery/items/${candidateId}${query}`, {
    method: 'DELETE',
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `移除交付歌曲失败: ${resp.statusText}`);
  return data;
}

export async function restoreDeliverySong(candidateId: number, playlistName?: string): Promise<{ success: boolean }> {
  const query = playlistName ? `?playlist_name=${encodeURIComponent(playlistName)}` : '';
  const resp = await fetch(`${API_BASE}/publication/delivery/items/${candidateId}/restore${query}`, {
    method: 'POST',
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `恢复交付歌曲失败: ${resp.statusText}`);
  return data;
}

export async function fetchArtistKnowledge(
  artist: string,
  autoCollect: boolean = false,
  songTitle: string = '',
  albumTitle: string = ''
): Promise<{ found: boolean; knowledge?: import('./types').ArtistKnowledge; status?: string; local_missing?: boolean }> {
  const params = new URLSearchParams({
    artist,
    auto_collect: String(autoCollect),
    song_title: songTitle,
    album_title: albumTitle,
  });
  const resp = await fetch(`${API_BASE}/artist-knowledge?${params.toString()}`);
  if (!resp.ok) throw new Error(`艺人资料加载失败: ${resp.statusText}`);
  return await resp.json();
}

export async function fetchArtistKnowledgeBatch(
  artists: string[],
  autoCollect: boolean = false
): Promise<Record<string, import('./types').ArtistKnowledge | null>> {
  if (!artists.length) return {};
  const resp = await fetch(`${API_BASE}/artist-knowledge/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artists, auto_collect: autoCollect }),
  });
  if (!resp.ok) throw new Error(`批量艺人资料加载失败: ${resp.statusText}`);
  const data = await resp.json();
  return data.results || {};
}

export async function triggerCollectArtistKnowledge(
  artistName: string,
  songTitle: string = '',
  albumTitle: string = '',
  force: boolean = true
): Promise<{ success: boolean; queued: boolean; artist_name: string }> {
  const resp = await fetch(`${API_BASE}/artist-knowledge/collect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      artist_name: artistName,
      song_title: songTitle,
      album_title: albumTitle,
      force,
    }),
  });
  if (!resp.ok) throw new Error(`触发艺人收集失败: ${resp.statusText}`);
  return await resp.json();
}
