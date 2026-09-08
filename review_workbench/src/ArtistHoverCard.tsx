import React, { useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Clock,
  ExternalLink,
  HelpCircle,
  Info,
  Loader2,
  MapPin,
  Music2,
  RotateCw,
  Users,
} from 'lucide-react';
import { fetchArtistKnowledge, triggerCollectArtistKnowledge } from './api';
import { ArtistKnowledge } from './types';

// In-memory frontend cache across components to avoid repeated network calls
const knowledgeCache: Map<string, ArtistKnowledge | null> = new Map();
const inflightPromises: Map<string, Promise<ArtistKnowledge | null>> = new Map();

function getPlatformLabel(url: string): { label: string; color: string } {
  const lower = url.toLowerCase();
  if (lower.includes('spotify.com')) return { label: 'Spotify', color: 'text-emerald-400 border-emerald-800/60 bg-emerald-950/40' };
  if (lower.includes('instagram.com')) return { label: 'Instagram', color: 'text-pink-400 border-pink-800/60 bg-pink-950/40' };
  if (lower.includes('youtube.com') || lower.includes('youtu.be')) return { label: 'YouTube', color: 'text-red-400 border-red-800/60 bg-red-950/40' };
  if (lower.includes('wikipedia.org')) return { label: 'Wikipedia', color: 'text-sky-300 border-sky-800/60 bg-sky-950/40' };
  if (lower.includes('streetvoice.')) return { label: 'StreetVoice', color: 'text-orange-400 border-orange-800/60 bg-orange-950/40' };
  if (lower.includes('bandcamp.com')) return { label: 'Bandcamp', color: 'text-teal-400 border-teal-800/60 bg-teal-950/40' };
  if (lower.includes('music.apple.com')) return { label: 'Apple Music', color: 'text-rose-400 border-rose-800/60 bg-rose-950/40' };
  if (lower.includes('douban.com')) return { label: '豆瓣音乐', color: 'text-green-400 border-green-800/60 bg-green-950/40' };
  if (lower.includes('discogs.com')) return { label: 'Discogs', color: 'text-amber-400 border-amber-800/60 bg-amber-950/40' };
  if (lower.includes('musicbrainz.org')) return { label: 'MusicBrainz', color: 'text-indigo-400 border-indigo-800/60 bg-indigo-950/40' };
  return { label: '网络直链', color: 'text-cyan-400 border-cyan-800/60 bg-cyan-950/40' };
}

export function clearKnowledgeCache(artistName: string): void {
  const norm = artistName.trim().toLowerCase();
  knowledgeCache.delete(norm);
  inflightPromises.delete(norm);
}

export async function getOrFetchKnowledge(
  artistName: string,
  songTitle: string = '',
  albumTitle: string = '',
  force: boolean = false
): Promise<ArtistKnowledge | null> {
  const norm = artistName.trim();
  if (!norm) return null;
  const key = norm.toLowerCase();

  if (!force && knowledgeCache.has(key)) {
    return knowledgeCache.get(key) || null;
  }
  if (!force && inflightPromises.has(key)) {
    return inflightPromises.get(key)!;
  }

  // Hovering must strictly read local database only (autoCollect=false)
  const promise = fetchArtistKnowledge(norm, false, songTitle, albumTitle)
    .then(res => {
      if (res.found && res.knowledge) {
        if (res.knowledge.status === 'completed' || res.knowledge.status === 'sparse') {
          knowledgeCache.set(key, res.knowledge);
        }
        return res.knowledge;
      }
      if (res.status === 'collecting' || res.status === 'pending') {
        return {
          artist_name: norm,
          display_name: norm,
          factual_summary: '',
          sources: [],
          uncertainty: 'medium',
          identity_context: {},
          status: 'collecting' as const,
        };
      }
      if (res.status === 'failed' || res.knowledge?.status === 'failed') {
        return {
          artist_name: norm,
          display_name: norm,
          factual_summary: '',
          sources: [],
          uncertainty: 'high',
          identity_context: {},
          status: 'failed' as const,
          error: (res as any).error || res.knowledge?.error || '检索未完成，请点击重试',
        };
      }
      // Not found in local database
      const notFoundRecord: ArtistKnowledge = {
        artist_name: norm,
        display_name: norm,
        factual_summary: '',
        sources: [],
        uncertainty: 'high',
        identity_context: {},
        status: 'not_found' as const,
      };
      // Do not cache a miss permanently: the ingestion/backfill worker may
      // populate the local library after this hover. The next hover should
      // re-read SQLite, still without making any network request.
      return notFoundRecord;
    })
    .catch(err => {
      return {
        artist_name: norm,
        display_name: norm,
        factual_summary: '',
        sources: [],
        uncertainty: 'high',
        identity_context: {},
        status: 'failed' as const,
        error: String(err?.message || err || '网络连接异常'),
      };
    })
    .finally(() => {
      inflightPromises.delete(key);
    });

  inflightPromises.set(key, promise);
  return promise;
}

export function ArtistHoverCardContent({
  knowledge,
  loading,
  artistName,
  onCollect,
  onRetry,
}: {
  knowledge: ArtistKnowledge | null;
  loading: boolean;
  artistName: string;
  onCollect?: () => void;
  onRetry?: () => void;
}) {
  const isCollecting = knowledge?.status === 'collecting' || knowledge?.status === 'pending';
  const isFailed = knowledge?.status === 'failed';
  const isNotFound = !loading && (!knowledge || knowledge.status === 'not_found' || knowledge.status === 'local_missing');

  if (loading) {
    return (
      <div className="w-84 rounded-lg border border-[#2d3348] bg-[#121520] p-4 text-xs text-slate-300 shadow-2xl backdrop-blur-md">
        <div className="flex items-center gap-2 text-cyan-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="font-medium">正在读取本地资料库…</span>
        </div>
        <div className="mt-2 font-mono text-[11px] text-slate-500">{artistName}</div>
      </div>
    );
  }

  if (isCollecting) {
    return (
      <div className="w-84 rounded-lg border border-[#2d3348] bg-[#121520] p-4 text-xs text-slate-300 shadow-2xl backdrop-blur-md">
        <div className="flex items-center gap-2 text-cyan-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="font-medium">正在后台收集艺人资料…</span>
        </div>
        <div className="mt-1 text-[10px] text-slate-400">
          检索覆盖 Google、Spotify、Instagram、YouTube、StreetVoice 等多平台
        </div>
        <div className="mt-2 font-mono text-[11px] text-slate-500">{artistName}</div>
      </div>
    );
  }

  if (isFailed) {
    const handleAction = onRetry || onCollect;
    return (
      <div className="w-84 max-w-sm rounded-lg border border-amber-900/60 bg-[#141218] p-3.5 text-xs text-slate-200 shadow-2xl select-text">
        <div className="flex items-start justify-between gap-2 border-b border-[#2b222d] pb-2">
          <div className="flex items-center gap-1.5 font-bold text-slate-100 truncate">
            <Music2 className="h-3.5 w-3.5 text-amber-400 shrink-0" />
            <span className="truncate">{artistName}</span>
          </div>
          <span className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] border border-amber-700/60 bg-amber-950/40 text-amber-300">
            ⚠️ 检索未完成
          </span>
        </div>
        <div className="mt-2 text-[11px] leading-relaxed text-amber-300/90">
          本次网络检索未能返回有效结构化数据：{knowledge?.error || '服务响应超时或解析异常'}
        </div>
        <div className="mt-2 flex items-center justify-between">
          <div className="text-[9px] text-slate-500">注意：搜索失败不等于无资料。</div>
          {handleAction && (
            <button
              onClick={handleAction}
              className="flex items-center gap-1 rounded bg-blue-600/30 hover:bg-blue-600/50 border border-blue-500/40 px-2 py-0.5 text-[10px] text-blue-200 cursor-pointer"
            >
              <RotateCw className="h-2.5 w-2.5" />
              重新检索
            </button>
          )}
        </div>
      </div>
    );
  }

  if (isNotFound) {
    const handleAction = onCollect || onRetry;
    return (
      <div className="w-84 max-w-sm rounded-lg border border-[#2d3348] bg-[#121520] p-3.5 text-xs text-slate-200 shadow-2xl select-text">
        <div className="flex items-start justify-between gap-2 border-b border-[#23283a] pb-2">
          <div className="flex items-center gap-1.5 font-bold text-slate-100 truncate">
            <Music2 className="h-3.5 w-3.5 text-slate-400 shrink-0" />
            <span className="truncate">{artistName}</span>
          </div>
          <span className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] border border-slate-700/60 bg-slate-800/40 text-slate-400">
            本地未收录
          </span>
        </div>
        <div className="mt-2.5 text-[11px] leading-relaxed text-slate-300 font-medium">
          本地资料库尚未收录
        </div>
        <div className="mt-1 text-[10px] text-slate-500 leading-relaxed">
          悬停仅读取本地数据库，不进行实时联网搜索。系统正在后台自动异步收录，亦可点击下方按钮手动加速。
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-[#1e2233] pt-2">
          <div className="text-[9px] text-slate-500">点击后由 AGy 后台异步执行</div>
          {handleAction && (
            <button
              onClick={handleAction}
              className="flex items-center gap-1 rounded bg-blue-600/30 hover:bg-blue-600/50 border border-blue-500/40 px-2.5 py-1 text-[10px] font-medium text-blue-200 transition-colors cursor-pointer"
            >
              <RotateCw className="h-2.5 w-2.5" />
              后台收集
            </button>
          )}
        </div>
      </div>
    );
  }

  const identity = knowledge?.identity_context || {};
  const genres = Array.isArray(identity.genre)
    ? identity.genre
    : identity.genre
    ? [String(identity.genre)]
    : [];
  const members = Array.isArray(identity.members) ? identity.members : [];
  const sources = knowledge?.sources || [];
  const unc = knowledge?.uncertainty || 'medium';
  const isSparse = knowledge?.status === 'sparse' || unc === 'high';

  return (
    <div className="w-88 max-w-md rounded-lg border border-[#333a52] bg-[#10121b] p-3.5 text-xs text-slate-200 shadow-2xl select-text">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 border-b border-[#23283a] pb-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 font-bold text-slate-100 truncate">
            <Music2 className="h-3.5 w-3.5 text-blue-400 shrink-0" />
            <span className="truncate">{knowledge?.display_name || artistName}</span>
          </div>
          {identity.type && (
            <span className="mt-0.5 inline-block text-[10px] text-slate-400 font-mono">
              {identity.type} {identity.active_years ? `· ${identity.active_years}` : ''}
            </span>
          )}
        </div>
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] border ${
            unc === 'low'
              ? 'border-emerald-700/60 bg-emerald-950/40 text-emerald-300'
              : isSparse
              ? 'border-amber-700/60 bg-amber-950/40 text-amber-300'
              : 'border-cyan-700/60 bg-cyan-950/40 text-cyan-300'
          }`}
        >
          {unc === 'low' ? '✓ 高置信收录' : isSparse ? '⚠️ 资料稀疏' : 'ℹ 部分档案'}
        </span>
      </div>

      {/* Meta tags */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
        {identity.origin && (
          <span className="flex items-center gap-0.5 rounded bg-[#1a1e2c] border border-[#2b3145] px-1.5 py-0.5 text-slate-300">
            <MapPin className="h-2.5 w-2.5 text-rose-400" />
            {identity.origin}
          </span>
        )}
        {genres.slice(0, 4).map((g: string, i: number) => (
          <span key={i} className="rounded bg-[#1b2130] border border-[#2e374f] px-1.5 py-0.5 text-blue-300">
            {g}
          </span>
        ))}
      </div>

      {/* Summary */}
      <div className="mt-2 text-[11px] leading-relaxed text-slate-300">
        {knowledge?.factual_summary || '暂无详细公开背景信息；该艺人可能属于早期独立发行或地下厂牌。'}
      </div>

      {/* Members if available */}
      {members.length > 0 && (
        <div className="mt-2 rounded bg-[#0b0c13] p-1.5 border border-[#1e2233] text-[10px] text-slate-400">
          <div className="flex items-center gap-1 text-slate-500 font-medium mb-0.5">
            <Users className="h-2.5 w-2.5 text-cyan-400" />
            <span>成员构成:</span>
          </div>
          <div className="truncate">{members.join(' / ')}</div>
        </div>
      )}

      {/* Real sources with platform badges */}
      <div className="mt-2.5 border-t border-[#202434] pt-2">
        <div className="flex items-center justify-between text-[10px] text-slate-400">
          <span className="flex items-center gap-1 font-medium">
            <BookOpen className="h-3 w-3 text-slate-400" />
            <span>核验来源 ({sources.length}):</span>
          </span>
          {sources.length === 0 && <span className="text-slate-600">无外部直链</span>}
        </div>
        {sources.length > 0 && (
          <div className="mt-1 flex flex-col gap-1 max-h-24 overflow-y-auto pr-1">
            {sources.map((url, i) => {
              const platform = getPlatformLabel(url);
              return (
                <a
                  key={i}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 text-[10px] text-slate-300 hover:text-cyan-300 hover:underline truncate group"
                >
                  <span className={`rounded border px-1 py-0.2 text-[9px] font-mono shrink-0 ${platform.color}`}>
                    {platform.label}
                  </span>
                  <ExternalLink className="h-2.5 w-2.5 shrink-0 text-slate-500 group-hover:text-cyan-400" />
                  <span className="truncate text-slate-400 group-hover:text-cyan-300">{url}</span>
                </a>
              );
            })}
          </div>
        )}
      </div>

      {/* Disclaimer / Principle */}
      <div className="mt-2 rounded bg-[#171a25]/60 p-1.5 text-[9px] text-slate-500 leading-tight">
        💡 提示：独立/小众发行若公开资料稀疏属正常现象，不作为候选排除理由。
      </div>
    </div>
  );
}

export function ArtistHoverTrigger({
  artistName,
  songTitle,
  albumTitle,
  children,
  className = '',
}: {
  artistName: string;
  songTitle?: string;
  albumTitle?: string;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [knowledge, setKnowledge] = useState<ArtistKnowledge | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<number | null>(null);
  const pollIntervalRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollIntervalRef.current) {
      window.clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  };

  const startPolling = (name: string, sTitle?: string, aTitle?: string) => {
    stopPolling();
    let pollCount = 0;
    pollIntervalRef.current = window.setInterval(async () => {
      pollCount++;
      try {
        const res = await fetchArtistKnowledge(name, false, sTitle || '', aTitle || '');
        if (res.found && res.knowledge && (res.knowledge.status === 'completed' || res.knowledge.status === 'sparse')) {
          knowledgeCache.set(name.toLowerCase(), res.knowledge);
          setKnowledge(res.knowledge);
          setLoading(false);
          stopPolling();
          return;
        }
        if (res.status === 'failed' || res.knowledge?.status === 'failed') {
          setKnowledge((res.knowledge || {
            artist_name: name,
            display_name: name,
            factual_summary: '',
            sources: [],
            uncertainty: 'high',
            identity_context: {},
            status: 'failed' as const,
          }) as ArtistKnowledge);
          setLoading(false);
          stopPolling();
          return;
        }
      } catch {
        // Continue polling
      }
      if (pollCount >= 45) {
        stopPolling();
      }
    }, 2000);
  };

  const loadData = (force: boolean = false) => {
    setLoading(true);
    // Read-only local fetch on hover; polls while hovered if background collection is active
    void getOrFetchKnowledge(artistName, songTitle, albumTitle, force).then(data => {
      setKnowledge(data);
      setLoading(false);
      if (data?.status === 'collecting') {
        startPolling(artistName, songTitle, albumTitle);
      }
    });
  };

  const handleMouseEnter = () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      setOpen(true);
      if (!knowledge) {
        loadData(false);
      }
    }, 200);
  };

  const handleMouseLeave = () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      setOpen(false);
      stopPolling();
    }, 200);
  };

  const handleManualCollect = async () => {
    setLoading(true);
    stopPolling();
    clearKnowledgeCache(artistName);
    try {
      await triggerCollectArtistKnowledge(artistName, songTitle, albumTitle, true);
      const collectingRecord: ArtistKnowledge = {
        artist_name: artistName,
        display_name: artistName,
        factual_summary: '',
        sources: [],
        uncertainty: 'medium',
        identity_context: {},
        status: 'collecting',
      };
      setKnowledge(collectingRecord);
      setLoading(false);
      // ONLY start polling upon user-initiated collection!
      startPolling(artistName, songTitle, albumTitle);
    } catch (err: any) {
      setLoading(false);
      setKnowledge({
        artist_name: artistName,
        display_name: artistName,
        factual_summary: '',
        sources: [],
        uncertainty: 'high',
        identity_context: {},
        status: 'failed',
        error: String(err?.message || err || '收集请求失败'),
      });
    }
  };

  useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
      stopPolling();
    };
  }, []);

  return (
    <div
      className={`relative inline-block ${className}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 shadow-2xl">
          <ArtistHoverCardContent
            knowledge={knowledge}
            loading={loading}
            artistName={artistName}
            onCollect={handleManualCollect}
            onRetry={handleManualCollect}
          />
        </div>
      )}
    </div>
  );
}
