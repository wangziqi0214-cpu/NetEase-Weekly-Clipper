import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import DataEditor, { GridCell, GridCellKind, GridColumn, Item, GridSelection, CompactSelection, Theme, DataEditorRef } from '@glideapps/glide-data-grid';
import { CheckCircle2, XCircle, RotateCcw, Play, Pause, ExternalLink, Layers, Sparkles, Search, Disc3, PanelRightClose, PanelRightOpen, Music2, Rocket, ShieldAlert } from 'lucide-react';
import { ArtistKnowledge, CandidateItem, DatabaseStats, ReviewStatus } from './types';
import { fetchCandidates, fetchStats, updateCandidateBatchStatus, triggerCollectArtistKnowledge, fetchArtistKnowledge } from './api';
import { ArtistHoverCardContent, getOrFetchKnowledge, clearKnowledgeCache } from './ArtistHoverCard';
import DeliveryWorkspace from './DeliveryWorkspace';
import SafetyPanel from './SafetyPanel';

const workbenchGridTheme: Partial<Theme> = {
  accentColor:'#3b82f6', accentFg:'#fff', accentLight:'rgba(59,130,246,.15)', textDark:'#f1f5f9', textMedium:'#94a3b8', textLight:'#64748b',
  bgCell:'#12141c', bgCellMedium:'#181b26', bgHeader:'#1a1d28', bgHeaderHasFocus:'#242938', bgHeaderHovered:'#222634', textHeader:'#cbd5e1',
  textHeaderSelected:'#fff', borderColor:'#232736', drilldownBorder:'#3b82f6', fontFamily:'Inter,-apple-system,BlinkMacSystemFont,sans-serif',
  baseFontStyle:'12px Inter', headerFontStyle:'600 12px Inter', lineHeight:1.2,
};

const COLUMNS: GridColumn[] = [
  { id:'cover', title:'封面', width:50 }, { id:'title', title:'歌曲名称 & Feat', width:220 },
  { id:'artist_names', title:'歌手 / 合作艺人', width:170 }, { id:'screening', title:'机筛分层与规则反馈', width:210 },
  { id:'platforms', title:'来源平台', width:130 }, { id:'status', title:'审核状态', width:95 }, { id:'album_title', title:'专辑名称', width:160 },
  { id:'release_type', title:'类型', width:70 }, { id:'release_date', title:'发行日期', width:95 }, { id:'duration', title:'时长', width:65 },
];

type ContextMenuState = { x:number; y:number; cell:Item; candidate:CandidateItem; cellText:string };
type TextOverlayState = { text:string; x:number; y:number; width:number; height:number };

export default function App() {
  const [workspace,setWorkspace]=useState<'review'|'delivery'>('review'); const [safetyOpen,setSafetyOpen]=useState(false);
  const [candidates,setCandidates]=useState<CandidateItem[]>([]); const [stats,setStats]=useState<DatabaseStats|null>(null);
  const [loading,setLoading]=useState(true); const [error,setError]=useState<string|null>(null);
  const [viewMeta,setViewMeta]=useState({rawCount:0,hiddenIncomplete:0});
  const [platformFilter,setPlatformFilter]=useState('all'); const [statusFilter,setStatusFilter]=useState('pending');
  const [tierFilter,setTierFilter]=useState('all'); const [keyword,setKeyword]=useState(''); const [enableDedup,setEnableDedup]=useState(true);
  const [gridSelection,setGridSelection]=useState<GridSelection>({columns:CompactSelection.empty(),rows:CompactSelection.empty(),current:undefined});
  const [activeCandidate,setActiveCandidate]=useState<CandidateItem|null>(null); const [isDrawerOpen,setIsDrawerOpen]=useState(true);
  const [playingTrack,setPlayingTrack]=useState<CandidateItem|null>(null); const [isPlaying,setIsPlaying]=useState(false); const audioRef=useRef<HTMLAudioElement|null>(null);
  const dataEditorRef=useRef<DataEditorRef|null>(null); const gridHostRef=useRef<HTMLDivElement|null>(null); const searchRef=useRef<HTMLInputElement|null>(null); const textOverlayRef=useRef<HTMLInputElement|null>(null);
  const [contextMenu,setContextMenu]=useState<ContextMenuState|null>(null); const [textOverlay,setTextOverlay]=useState<TextOverlayState|null>(null); const [toast,setToast]=useState('');
  const [tableHoveredArtist, setTableHoveredArtist] = useState<{
    artistName: string;
    songTitle?: string;
    x: number;
    y: number;
  } | null>(null);
  const [hoverKnowledge, setHoverKnowledge] = useState<ArtistKnowledge | null>(null);
  const [hoverLoading, setHoverLoading] = useState(false);
  const hoverTimerRef = useRef<number | null>(null);
  const hoverPollIntervalRef = useRef<number | null>(null);

  const stopHoverPolling = useCallback(() => {
    if (hoverPollIntervalRef.current) {
      window.clearInterval(hoverPollIntervalRef.current);
      hoverPollIntervalRef.current = null;
    }
  }, []);

  const startHoverPolling = useCallback((artistName: string, songTitle: string = '') => {
    stopHoverPolling();
    let pollCount = 0;
    hoverPollIntervalRef.current = window.setInterval(async () => {
      pollCount++;
      try {
        const res = await fetchArtistKnowledge(artistName, false, songTitle || '', '');
        if (res.found && res.knowledge && (res.knowledge.status === 'completed' || res.knowledge.status === 'sparse')) {
          setHoverKnowledge(res.knowledge);
          stopHoverPolling();
          return;
        }
        if (res.status === 'failed' || res.knowledge?.status === 'failed') {
          setHoverKnowledge((res.knowledge || {
            artist_name: artistName,
            display_name: artistName,
            factual_summary: '',
            sources: [],
            uncertainty: 'high',
            identity_context: {},
            status: 'failed',
            error: (res as any)?.error || '收集失败',
          }) as ArtistKnowledge);
          stopHoverPolling();
          return;
        }
      } catch {
        // Continue polling
      }
      if (pollCount >= 45) {
        stopHoverPolling();
      }
    }, 2000);
  }, [stopHoverPolling]);

  const handleCollectTableArtist = useCallback(async () => {
    if (!tableHoveredArtist) return;
    const { artistName, songTitle } = tableHoveredArtist;
    setHoverLoading(true);
    stopHoverPolling();
    clearKnowledgeCache(artistName);
    try {
      await triggerCollectArtistKnowledge(artistName, songTitle || '', '', true);
      const collectingRecord: ArtistKnowledge = {
        artist_name: artistName,
        display_name: artistName,
        factual_summary: '',
        sources: [],
        uncertainty: 'medium',
        identity_context: {},
        status: 'collecting',
      };
      setHoverKnowledge(collectingRecord);
      setHoverLoading(false);
      startHoverPolling(artistName, songTitle || '');
    } catch (err: any) {
      setHoverLoading(false);
      setHoverKnowledge({
        artist_name: artistName,
        display_name: artistName,
        factual_summary: '',
        sources: [],
        uncertainty: 'high',
        identity_context: {},
        status: 'failed',
        error: String(err?.message || err || '触发后台收集失败'),
      });
    }
  }, [tableHoveredArtist, stopHoverPolling, startHoverPolling]);

  useEffect(() => {
    return () => {
      stopHoverPolling();
    };
  }, [stopHoverPolling]);

  const handleItemHovered = useCallback((args: any) => {
    if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current);
    if (args.kind === 'cell' && args.location && COLUMNS[args.location[0]]?.id === 'artist_names') {
      const candidate = candidates[args.location[1]];
      if (candidate?.artist_names) {
        const host = gridHostRef.current?.getBoundingClientRect();
        const x = (host?.left || 0) + (args.bounds?.x || 0);
        const y = (host?.top || 0) + (args.bounds?.y || 0) + (args.bounds?.height || 0);
        const artist = candidate.artist_names;
        const song = candidate.title;
        hoverTimerRef.current = window.setTimeout(() => {
          setTableHoveredArtist({ artistName: artist, songTitle: song, x, y });
          setHoverLoading(true);
          void getOrFetchKnowledge(artist, song).then(k => {
            setHoverKnowledge(k);
            setHoverLoading(false);
            if (k?.status === 'collecting') {
              startHoverPolling(artist, song);
            }
          });
        }, 250);
        return;
      }
    }
    hoverTimerRef.current = window.setTimeout(() => {
      setTableHoveredArtist(null);
      stopHoverPolling();
    }, 150);
  }, [candidates, stopHoverPolling]);

  const loadData=useCallback(async()=>{setLoading(true);setError(null);try{const [page,summary]=await Promise.all([fetchCandidates({platform:platformFilter,status:statusFilter,tier:tierFilter,keyword,dedup:enableDedup}),fetchStats()]);setCandidates(page.items);setViewMeta({rawCount:page.raw_count,hiddenIncomplete:page.hidden_incomplete});setStats(summary);}catch(err){setError(err instanceof Error?err.message:'加载候选歌曲失败');}finally{setLoading(false);}},[platformFilter,statusFilter,tierFilter,keyword,enableDedup]);
  useEffect(()=>{void loadData();},[loadData]);
  const formatDuration=(ms?:number)=>{if(!ms||ms<=0)return '--:--';const total=Math.floor(ms/1000);return `${Math.floor(total/60).toString().padStart(2,'0')}:${(total%60).toString().padStart(2,'0')}`;};

  const selectedRows=useCallback(()=>{const rows=new Set<number>();for(let i=0;i<candidates.length;i++)if(gridSelection.rows.hasIndex(i))rows.add(i);const range=gridSelection.current?.range;if(range)for(let row=range.y;row<range.y+range.height;row++)if(row>=0&&row<candidates.length)rows.add(row);if(rows.size===0&&gridSelection.current?.cell)rows.add(gridSelection.current.cell[1]);return rows;},[candidates.length,gridSelection]);

  const getCellContent=useCallback(([colIndex,rowIndex]:Item):GridCell=>{const c=candidates[rowIndex];if(!c)return{kind:GridCellKind.Loading,allowOverlay:false};const id=COLUMNS[colIndex].id;switch(id){
    case 'cover':return{kind:GridCellKind.Image,data:[c.cover_url||'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="%2364748b" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>'],allowOverlay:false,displayData:[c.cover_url||'']};
    case 'title':return{kind:GridCellKind.Text,data:c.title,displayData:c.featured_artists?.length?`${c.clean_title} (feat. ${c.featured_artists.join(', ')})`:c.title,allowOverlay:false};
    case 'artist_names':return{kind:GridCellKind.Text,data:c.artist_names,displayData:c.artist_names,allowOverlay:false};
    case 'screening':{const prefix=c.screening_tier==='TIER_1_HOT'?'🔥 3源高热':c.screening_tier==='TIER_2_RULE'?'🎯 规则命中':c.screening_tier==='TIER_3_ATTENTION'?'⚠️ 需核验':'❌ 噪音';const reason=c.screening_reasons?.join(' | ')||'';return{kind:GridCellKind.Text,data:`${prefix} ${reason}`.trim(),displayData:`${prefix} ${reason}`.trim(),allowOverlay:false};}
    case 'platforms':return{kind:GridCellKind.Text,data:c.platforms_display,displayData:c.dedup_count>1?`${c.platforms_display} (${c.dedup_count}源)`:c.platforms_display,allowOverlay:false};
    case 'status':{const labels:Record<string,string>={pending:'⏳ 待审核',approved:'✅ 已通过',rejected:'❌ 已否决',deferred:'⏸️ 存疑暂缓',machine_filtered:'🤖 机器排除'};return{kind:GridCellKind.Text,data:c.status,displayData:labels[c.status]||c.status,allowOverlay:false};}
    case 'album_title':return{kind:GridCellKind.Text,data:c.album_title||'无专辑',displayData:c.album_title||'无专辑',allowOverlay:false};
    case 'release_type':return{kind:GridCellKind.Text,data:(c.release_type||'single').toUpperCase(),displayData:(c.release_type||'single').toUpperCase(),allowOverlay:false};
    case 'release_date':return{kind:GridCellKind.Text,data:c.release_date||'--',displayData:c.release_date||'--',allowOverlay:false};
    case 'duration':return{kind:GridCellKind.Text,data:formatDuration(c.duration_ms),displayData:formatDuration(c.duration_ms),allowOverlay:false,contentAlign:'right'};
    default:return{kind:GridCellKind.Text,data:'',displayData:'',allowOverlay:false};}},[candidates]);

  const readCellText=useCallback((cell:Item)=>{const value=getCellContent(cell);if(value.kind===GridCellKind.Text)return value.displayData;if(value.kind===GridCellKind.Image)return value.displayData?.[0]||'';return '';},[getCellContent]);
  const copyText=useCallback(async(text:string,label='已复制')=>{if(!text){setToast('当前内容为空');return;}try{await navigator.clipboard.writeText(text);setToast(label);}catch{const area=document.createElement('textarea');area.value=text;document.body.appendChild(area);area.select();document.execCommand('copy');area.remove();setToast(label);}},[]);
  const openTextSelector=useCallback((cell:Item)=>{const text=readCellText(cell);const bounds=dataEditorRef.current?.getBounds(cell[0],cell[1]);if(!text||!bounds)return;setContextMenu(null);setTextOverlay({text,x:bounds.x,y:bounds.y,width:Math.max(bounds.width,180),height:bounds.height});},[readCellText]);
  const openContextMenu=useCallback((cell:Item,event:{preventDefault:()=>void;localEventX:number;localEventY:number})=>{event.preventDefault();const candidate=candidates[cell[1]];if(!candidate)return;const chosen=selectedRows();if(!chosen.has(cell[1]))setGridSelection({columns:CompactSelection.empty(),rows:CompactSelection.empty(),current:{cell,range:{x:cell[0],y:cell[1],width:1,height:1},rangeStack:[]}});setActiveCandidate(candidate);const host=gridHostRef.current?.getBoundingClientRect();setContextMenu({x:(host?.left||0)+event.localEventX,y:(host?.top||0)+event.localEventY,cell,candidate,cellText:readCellText(cell)});},[candidates,selectedRows,readCellText]);

  useEffect(()=>{if(textOverlay)requestAnimationFrame(()=>textOverlayRef.current?.focus());},[textOverlay]);
  useEffect(()=>{if(!toast)return;const timer=window.setTimeout(()=>setToast(''),1800);return()=>window.clearTimeout(timer);},[toast]);
  useEffect(()=>{const close=()=>setContextMenu(null);window.addEventListener('click',close);return()=>window.removeEventListener('click',close);},[]);

  useEffect(()=>{if(gridSelection.current?.cell){const row=gridSelection.current.cell[1];if(candidates[row])setActiveCandidate(candidates[row]);}},[gridSelection.current,candidates]);
  const clearSelection=useCallback(()=>{setGridSelection({columns:CompactSelection.empty(),rows:CompactSelection.empty(),current:undefined});setActiveCandidate(null);},[]);
  const showStatus=useCallback((status:string)=>{setStatusFilter(status);setPlatformFilter('all');setTierFilter('all');setKeyword('');clearSelection();},[clearSelection]);
  const selectAllRows=useCallback(()=>setGridSelection({columns:CompactSelection.empty(),rows:CompactSelection.fromSingleSelection([0,candidates.length]),current:undefined}),[candidates.length]);
  const selectionCount=selectedRows().size;
  const updateStatus=useCallback(async(status:ReviewStatus)=>{const targets=[...selectedRows()].map(index=>candidates[index]).filter((item):item is CandidateItem=>Boolean(item));if(!targets.length){alert('请先在表格中选中需要处理的歌曲。');return;}const ids=[...new Set(targets.flatMap(c=>c.raw_ids?.length?c.raw_ids:[c.id]))];try{await updateCandidateBatchStatus(ids,status);clearSelection();await loadData();}catch(err){alert(`审核操作失败: ${err instanceof Error?err.message:'更新失败'}`);}},[selectedRows,candidates,clearSelection,loadData]);
  const togglePlay=useCallback((track:CandidateItem)=>{if(!track.source_url){alert('当前歌曲暂无可用试听链接。');return;}if(playingTrack?.id===track.id){if(isPlaying){audioRef.current?.pause();setIsPlaying(false);}else{void audioRef.current?.play();setIsPlaying(true);}}else{setPlayingTrack(track);setIsPlaying(true);if(audioRef.current){audioRef.current.src=track.source_url;audioRef.current.play().catch(()=>setIsPlaying(false));}}},[playingTrack,isPlaying]);
  useEffect(()=>{const keydown=(e:KeyboardEvent)=>{if(e.target instanceof HTMLInputElement||e.target instanceof HTMLTextAreaElement)return;const key=e.key.toUpperCase();const command=e.metaKey||e.ctrlKey;if(key==='F'&&command){e.preventDefault();searchRef.current?.focus();}else if(key==='V'&&command){e.preventDefault();setToast('表格为只读；请把内容粘贴到搜索框');}else if(key==='A'&&command){e.preventDefault();selectAllRows();}else if((key==='ENTER'||key==='F2')&&gridSelection.current?.cell){e.preventDefault();openTextSelector(gridSelection.current.cell);}else if(key==='A'){e.preventDefault();void updateStatus('approved');}else if(key==='R'){e.preventDefault();void updateStatus('rejected');}else if(e.code==='Space'){e.preventDefault();if(activeCandidate)togglePlay(activeCandidate);}else if(key==='ESCAPE'){e.preventDefault();setContextMenu(null);setTextOverlay(null);clearSelection();}};window.addEventListener('keydown',keydown);return()=>window.removeEventListener('keydown',keydown);},[updateStatus,activeCandidate,togglePlay,clearSelection,selectAllRows,gridSelection.current,openTextSelector]);
  const tierCounts=useMemo(()=>candidates.reduce((acc,c)=>{if(c.screening_tier==='TIER_1_HOT')acc.TIER_1++;else if(c.screening_tier==='TIER_2_RULE')acc.TIER_2++;else if(c.screening_tier==='TIER_3_ATTENTION')acc.TIER_3++;else acc.TIER_4++;return acc;},{TIER_1:0,TIER_2:0,TIER_3:0,TIER_4:0}),[candidates]);

  return <div className="relative flex flex-col h-screen w-screen bg-[#090a0f] text-slate-100 select-none overflow-hidden">
    <header className="flex items-center justify-between px-4 h-14 bg-[#12141c] border-b border-[#232736] shrink-0">
      <div className="flex items-center space-x-3"><div className="flex items-center space-x-2 text-blue-400 font-bold tracking-wide"><Disc3 className="w-5 h-5 text-blue-500"/><span className="text-sm font-semibold tracking-wider text-slate-100">AGY CLIPPER <span className="text-xs px-1.5 py-0.5 bg-blue-950 text-blue-400 rounded border border-blue-800">PRO</span></span></div><div className="flex rounded border border-[#292e40] bg-[#0d0f16] p-0.5 text-xs"><button onClick={()=>setWorkspace('review')} className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 ${workspace==='review'?'bg-blue-600 text-white':'text-slate-500 hover:text-slate-200'}`}><Music2 className="h-3.5 w-3.5"/>候选审阅</button><button onClick={()=>setWorkspace('delivery')} className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 ${workspace==='delivery'?'bg-violet-600 text-white':'text-slate-500 hover:text-slate-200'}`}><Rocket className="h-3.5 w-3.5"/>本周交付</button></div></div>
      <div className={`${workspace==='review'?'flex':'hidden'} items-center space-x-3 text-xs font-mono`}>
        <KpiCard label="📦 全部候选" value={stats?.total} sub={`${stats?.reviewable_total??'--'} 可审核 · ${stats?.unique_total??'--'} 独立曲`} active={statusFilter==='all'} onClick={()=>showStatus('all')}/>
        <KpiCard label="⏳ 待人审" value={stats?.pending} sub={`${stats?.unique_pending??'--'} 独立曲`} active={statusFilter==='pending'} tone="blue" onClick={()=>showStatus('pending')}/>
        <KpiCard label="🤖 机筛拦截" value={stats?.machine_filtered} sub={`${stats?.unique_machine_filtered??'--'} 独立曲 · ${stats?.hidden_incomplete??0} 待补全`} active={statusFilter==='machine_filtered'} tone="amber" onClick={()=>showStatus('machine_filtered')}/>
        <KpiCard label="✅ 已通过" value={stats?.approved} sub={`${stats?.unique_approved??'--'} 独立曲`} active={statusFilter==='approved'} tone="emerald" onClick={()=>showStatus('approved')}/>
        <KpiCard label="❌ 已否决" value={stats?.rejected} sub={`${stats?.unique_rejected??'--'} 独立曲`} active={statusFilter==='rejected'} tone="rose" onClick={()=>showStatus('rejected')}/>
        <div className="h-4 w-px bg-[#2a2f42]"/><div className={`flex items-center space-x-1 px-2.5 py-1 rounded ${selectionCount?'bg-blue-600 text-white font-bold':'bg-[#181b26] text-slate-500 border border-[#232736]'}`}><span>已选:</span><span>{selectionCount} 首</span></div>
      </div>
      {workspace==='delivery'&&<button onClick={()=>setSafetyOpen(true)} className="flex items-center gap-1.5 rounded border border-rose-800/60 bg-rose-950/30 px-3 py-1.5 text-xs text-rose-300"><ShieldAlert className="h-3.5 w-3.5"/>机筛安全门：影子阻断</button>}
    </header>

    <div className="flex items-center justify-between px-4 py-2 bg-[#161822] border-b border-[#232736] shrink-0 text-xs gap-3">
      <div className="flex items-center space-x-2 overflow-x-auto py-0.5">
        <Segment values={[['all','全部平台'],['netease','网易云'],['qq','QQ音乐'],['kkbox','KKBOX']]} current={platformFilter} set={setPlatformFilter}/>
        <Segment values={[['all','全部状态'],['pending','待审核'],['approved','已通过'],['rejected','已否决'],['deferred','存疑暂缓'],['machine_filtered','机筛排除']]} current={statusFilter} set={setStatusFilter}/>
        <select value={tierFilter} onChange={e=>setTierFilter(e.target.value)} className="bg-[#10121a] text-slate-300 border border-[#232736] rounded px-2.5 py-1 outline-none focus:border-blue-500"><option value="all">全部机筛分层 ({candidates.length})</option><option value="TIER_1_HOT">🔥 Tier 1: 跨源高热 ({tierCounts.TIER_1})</option><option value="TIER_2_RULE">🎯 Tier 2: 规则命中 ({tierCounts.TIER_2})</option><option value="TIER_3_ATTENTION">⚠️ Tier 3: 需人工复核 ({tierCounts.TIER_3})</option><option value="TIER_4_NOISE">❌ Tier 4: 机器排除 ({tierCounts.TIER_4})</option></select>
        <button onClick={()=>setEnableDedup(v=>!v)} className={`flex items-center space-x-1 px-2.5 py-1 rounded border ${enableDedup?'bg-emerald-950/40 text-emerald-400 border-emerald-700/50':'bg-[#10121a] text-slate-500 border-[#232736]'}`}><Layers className="w-3.5 h-3.5"/><span>智能去重: {enableDedup?'ON':'OFF'}</span></button>
        <div className="relative flex items-center"><Search className="w-3.5 h-3.5 absolute left-2.5 text-slate-500"/><input ref={searchRef} placeholder="搜索歌名 / 歌手 / 专辑..." value={keyword} onChange={e=>setKeyword(e.target.value)} className="bg-[#10121a] text-slate-200 text-xs pl-8 pr-3 py-1 rounded border border-[#232736] w-48 focus:w-64 transition-all outline-none focus:border-blue-500 placeholder-slate-600"/></div>
      </div>
      <div className="flex items-center space-x-2 shrink-0">
        <button onClick={selectAllRows} className="px-2.5 py-1 bg-[#1c202e] hover:bg-[#252b3d] text-slate-300 rounded border border-[#2e354a]">全选当前 ({candidates.length})</button><button onClick={clearSelection} className="px-2 py-1 bg-[#1c202e] hover:bg-[#252b3d] text-slate-400 rounded border border-[#2e354a]">清空选择</button><div className="h-4 w-px bg-[#2e354a]"/>
        <ActionButton type="approve" onClick={()=>void updateStatus('approved')}/><ActionButton type="reject" onClick={()=>void updateStatus('rejected')}/><button onClick={()=>void updateStatus('pending')} className="p-1 bg-[#1c202e] hover:bg-[#252b3d] text-slate-400 rounded border border-[#2e354a]" title="批量重置为待审"><RotateCcw className="w-3.5 h-3.5"/></button>
        <button onClick={()=>setIsDrawerOpen(v=>!v)} className="p-1 text-slate-400 hover:text-slate-200 ml-1">{isDrawerOpen?<PanelRightClose className="w-4 h-4"/>:<PanelRightOpen className="w-4 h-4"/>}</button>
      </div>
    </div>

    <main className="flex flex-1 overflow-hidden relative">
      <div className="flex flex-1 h-full min-w-0 flex-col bg-[#12141c] overflow-hidden">
        <div className="flex h-7 shrink-0 items-center justify-between border-b border-[#232736] bg-[#10121a] px-3 text-[11px] text-slate-400">
          <span>当前展示 <strong className="text-slate-200">{candidates.length}</strong> 行{enableDedup&&<>（聚合自 <strong className="text-blue-300">{viewMeta.rawCount}</strong> 条可审核原始记录）</>}{viewMeta.hiddenIncomplete>0&&<span className="ml-2 text-amber-400">另有 {viewMeta.hiddenIncomplete} 条元数据待补全，暂不进入人审</span>}</span>
          <span>{enableDedup?'跨平台聚合视图':'原始平台记录视图'}</span>
        </div>
        <div ref={gridHostRef} className="relative flex-1 min-h-0 overflow-hidden">
          {loading&&<Overlay><Disc3 className="w-5 h-5 animate-spin"/><span>加载候选数据与跨平台聚合中...</span></Overlay>}
          {error&&<div className="absolute inset-0 bg-rose-950/80 flex flex-col items-center justify-center z-10 text-rose-200"><p className="font-semibold">{error}</p><button onClick={()=>void loadData()} className="mt-3 px-3 py-1 bg-rose-800 text-white rounded text-xs">重试加载</button></div>}
          <DataEditor ref={dataEditorRef} theme={workbenchGridTheme} columns={COLUMNS} rows={candidates.length} getCellContent={getCellContent} gridSelection={gridSelection} onGridSelectionChange={setGridSelection} onCellContextMenu={openContextMenu} onCellClicked={(cell,event)=>{if(event.isDoubleClick)openTextSelector(cell);}} onItemHovered={handleItemHovered} onPaste={false} rowHeight={38} headerHeight={32} smoothScrollX smoothScrollY isDraggable={false} rowMarkers="clickable-number" rowSelect="multi" columnSelect="none" rangeSelect="rect" fillHandle={false} width="100%" height="100%"/>
        </div>
      </div>
      {isDrawerOpen&&<aside className="w-96 bg-[#151722] border-l border-[#232736] flex flex-col h-full overflow-y-auto shrink-0 text-xs select-text">{activeCandidate?<Inspector candidate={activeCandidate} updateStatus={updateStatus}/>:<div className="h-full flex flex-col items-center justify-center text-slate-500 p-6 text-center"><Music2 className="w-8 h-8 mb-2 opacity-40"/><p>在表格中点击任意歌曲，查看跨平台数据比对与机筛特征。</p></div>}</aside>}
    </main>

    {contextMenu&&<div onClick={event=>{event.stopPropagation();setContextMenu(null);}} className="fixed z-50 w-64 overflow-hidden rounded-md border border-[#333a50] bg-[#171a24] py-1 text-xs text-slate-200 shadow-2xl" style={{left:Math.min(contextMenu.x,window.innerWidth-270),top:Math.min(contextMenu.y,window.innerHeight-330)}}>
      <MenuItem label="复制单元格文本" shortcut="⌘C" onClick={()=>void copyText(contextMenu.cellText)}/>
      <MenuItem label="复制为「歌名 - 歌手」" onClick={()=>void copyText(`${contextMenu.candidate.title} - ${contextMenu.candidate.artist_names}`)}/>
      <MenuItem label="复制歌曲原始链接" onClick={()=>void copyText(contextMenu.candidate.source_url)}/>
      <MenuItem label="复制平台原始 ID" onClick={()=>void copyText(contextMenu.candidate.raw_candidates?.[0]?.source_id||'')}/>
      <MenuItem label="粘贴到搜索框" shortcut="⌘V" onClick={async()=>{try{setKeyword(await navigator.clipboard.readText());searchRef.current?.focus();}catch{searchRef.current?.focus();setToast('请在搜索框中按 Cmd/Ctrl+V');}}}/>
      <div className="my-1 h-px bg-[#2a2f42]"/>
      <MenuItem label="标记所选为通过" shortcut="A" tone="text-emerald-400" onClick={()=>void updateStatus('approved')}/>
      <MenuItem label="标记所选为否决" shortcut="R" tone="text-rose-400" onClick={()=>void updateStatus('rejected')}/>
      <MenuItem label="标记所选为存疑暂缓" onClick={()=>void updateStatus('deferred')}/>
      <MenuItem label="恢复所选至待审核" onClick={()=>void updateStatus('pending')}/>
      <div className="my-1 h-px bg-[#2a2f42]"/>
      <MenuItem label="在原始音乐平台中打开" shortcut="↵" onClick={()=>contextMenu.candidate.source_url&&window.open(contextMenu.candidate.source_url,'_blank','noopener,noreferrer')}/>
    </div>}

    {textOverlay&&<input ref={textOverlayRef} readOnly value={textOverlay.text} onBlur={()=>setTextOverlay(null)} onKeyDown={event=>{if(event.key==='Escape'||event.key==='Enter'){event.preventDefault();setTextOverlay(null);dataEditorRef.current?.focus();}}} className="fixed z-40 rounded-sm border border-blue-500 bg-[#11141d] px-2 text-xs text-slate-100 shadow-xl outline-none select-text" style={{left:textOverlay.x,top:textOverlay.y,width:textOverlay.width,height:textOverlay.height}}/>}
    {toast&&<div className="fixed bottom-20 left-1/2 z-[60] -translate-x-1/2 rounded border border-[#3a4259] bg-[#1a1e2a] px-3 py-2 text-xs text-slate-200 shadow-xl">{toast}</div>}

    {tableHoveredArtist && (
      <div
        className="fixed z-50 pointer-events-auto shadow-2xl"
        style={{
          left: Math.min(tableHoveredArtist.x, window.innerWidth - 360),
          top: Math.min(tableHoveredArtist.y + 4, window.innerHeight - 280),
        }}
        onMouseEnter={() => {
          if (hoverTimerRef.current) window.clearTimeout(hoverTimerRef.current);
        }}
        onMouseLeave={() => {
          setTableHoveredArtist(null);
          stopHoverPolling();
        }}
      >
        <ArtistHoverCardContent
          artistName={tableHoveredArtist.artistName}
          knowledge={hoverKnowledge}
          loading={hoverLoading}
          onCollect={handleCollectTableArtist}
          onRetry={handleCollectTableArtist}
        />
      </div>
    )}

    <footer className="h-15 bg-[#12141c] border-t border-[#232736] px-4 flex items-center justify-between shrink-0 text-xs">
      <div className="flex items-center space-x-3 w-1/3 min-w-0"><button onClick={()=>activeCandidate&&togglePlay(activeCandidate)} className="w-9 h-9 rounded-full bg-blue-600 hover:bg-blue-500 text-white flex items-center justify-center shadow active:scale-95 shrink-0">{isPlaying?<Pause className="w-4 h-4 fill-current"/>:<Play className="w-4 h-4 fill-current ml-0.5"/>}</button><div className="min-w-0 flex-1"><div className="text-slate-200 font-semibold truncate">{playingTrack?`${playingTrack.title} - ${playingTrack.artist_names}`:'未播放音频'}</div><div className="text-slate-500 font-mono text-[11px] truncate">{playingTrack?`来源: ${playingTrack.platforms_display} | 时长: ${formatDuration(playingTrack.duration_ms)}`:'按 [Space] 快速试听选中歌曲'}</div></div></div>
      <div className="flex items-center space-x-2 w-1/3 max-w-sm"><span className="font-mono text-slate-500 text-[10px]">00:00</span><div className="flex-1 h-1.5 bg-[#232736] rounded-full overflow-hidden"><div className={`h-full bg-blue-500 ${isPlaying?'w-2/5':'w-0'}`}/></div><span className="font-mono text-slate-500 text-[10px]">{playingTrack?formatDuration(playingTrack.duration_ms):'--:--'}</span></div>
      <div className="flex items-center space-x-2.5 text-slate-400 text-[11px] shrink-0 font-mono"><KeyCap keyName="A" label="通过" tone="text-emerald-400"/><KeyCap keyName="R" label="否决" tone="text-rose-400"/><KeyCap keyName="Space" label="试听"/><KeyCap keyName="Esc" label="取消"/></div><audio ref={audioRef} onEnded={()=>setIsPlaying(false)} className="hidden"/>
    </footer>
    {workspace==='delivery'&&<div className="absolute inset-x-0 bottom-0 top-14 z-30 flex"><DeliveryWorkspace onOpenSafety={()=>setSafetyOpen(true)}/></div>}
    <SafetyPanel open={safetyOpen} onClose={()=>setSafetyOpen(false)}/>
  </div>;
}

function Segment({values,current,set}:{values:string[][];current:string;set:(value:string)=>void}){return <div className="flex items-center bg-[#10121a] rounded p-0.5 border border-[#232736]">{values.map(([id,label])=><button key={id} onClick={()=>set(id)} className={`px-2.5 py-1 rounded text-xs transition-all ${current===id?'bg-blue-600 text-white font-semibold shadow':'text-slate-400 hover:text-slate-200'}`}>{label}</button>)}</div>;}
function KpiCard({label,value,sub,active,tone,onClick}:{label:string;value?:number;sub:string;active:boolean;tone?:'blue'|'amber'|'emerald'|'rose';onClick:()=>void}){const activeClass=!active?'bg-[#161822] text-slate-300 border-[#272b3b] hover:border-slate-500':tone==='blue'?'bg-[#1e2436] text-blue-200 border-blue-500 ring-1 ring-blue-500/60':tone==='amber'?'bg-amber-950/30 text-amber-300 border-amber-500/60 ring-1 ring-amber-500/40':tone==='emerald'?'bg-emerald-950/30 text-emerald-300 border-emerald-700 ring-1 ring-emerald-500/30':tone==='rose'?'bg-rose-950/30 text-rose-300 border-rose-700 ring-1 ring-rose-500/30':'bg-[#202431] text-white border-slate-400 ring-1 ring-slate-400/40';return <button onClick={onClick} className={`flex min-w-[104px] cursor-pointer flex-col rounded border px-2.5 py-1 text-left transition-colors ${activeClass}`}><span className="flex w-full items-center justify-between gap-2"><span className="text-[10px] opacity-80">{label}</span><strong className="text-sm leading-none">{value??'--'}</strong></span><span className="mt-0.5 whitespace-nowrap text-[9px] opacity-60">{sub}</span></button>;}
function ActionButton({type,onClick}:{type:'approve'|'reject';onClick:()=>void}){const approve=type==='approve';return <button onClick={onClick} className={`flex items-center space-x-1 px-3 py-1 ${approve?'bg-emerald-600 hover:bg-emerald-500':'bg-rose-600 hover:bg-rose-500'} text-white font-semibold rounded shadow active:scale-95`}>{approve?<CheckCircle2 className="w-3.5 h-3.5"/>:<XCircle className="w-3.5 h-3.5"/>}<span>{approve?'批量通过':'批量否决'}</span><span className="kbd-chip ml-1">{approve?'A':'R'}</span></button>;}
function MenuItem({label,shortcut,tone='',onClick}:{label:string;shortcut?:string;tone?:string;onClick:()=>void}){return <button onClick={onClick} className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-[#252a3a]"><span className={tone}>{label}</span>{shortcut&&<span className="font-mono text-[10px] text-slate-500">{shortcut}</span>}</button>;}
function KeyCap({keyName,label,tone='' }:{keyName:string;label:string;tone?:string}){return <div className="flex items-center space-x-1"><span className="kbd-chip">{keyName}</span><span className={tone}>{label}</span></div>;}
function Overlay({children}:{children:React.ReactNode}){return <div className="absolute inset-0 bg-[#090a0f]/60 flex items-center justify-center z-10"><div className="flex items-center space-x-2 text-blue-400 font-mono text-sm">{children}</div></div>;}

function ArtistDrawerCard({
  artistName,
  songTitle,
  albumTitle,
}: {
  artistName: string;
  songTitle?: string;
  albumTitle?: string;
}) {
  const [knowledge, setKnowledge] = useState<ArtistKnowledge | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!artistName) return;
    setLoading(true);
    void getOrFetchKnowledge(artistName, songTitle, albumTitle).then(data => {
      setKnowledge(data);
      setLoading(false);
    });
  }, [artistName, songTitle, albumTitle]);

  const identity = knowledge?.identity_context || {};
  const genres = Array.isArray(identity.genre)
    ? identity.genre
    : identity.genre
    ? [String(identity.genre)]
    : [];
  const unc = knowledge?.uncertainty || 'medium';
  const isSparse = knowledge?.status === 'sparse' || unc === 'high';

  return (
    <div className="bg-[#1a1d2a] p-3 rounded-lg border border-[#272b3d] space-y-2">
      <div className="flex items-center justify-between text-slate-300 font-semibold border-b border-[#282d3f] pb-1.5">
        <div className="flex items-center space-x-1.5">
          <Music2 className="w-3.5 h-3.5 text-blue-400" />
          <span>本地艺人背景知识</span>
        </div>
        {knowledge && (
          <span
            className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${
              unc === 'low'
                ? 'border-emerald-700/60 bg-emerald-950/40 text-emerald-300'
                : isSparse
                ? 'border-amber-700/60 bg-amber-950/40 text-amber-300'
                : 'border-cyan-700/60 bg-cyan-950/40 text-cyan-300'
            }`}
          >
            {unc === 'low' ? '✓ 高置信' : isSparse ? '⚠️ 资料稀疏' : 'ℹ 部分收录'}
          </span>
        )}
      </div>
      {loading && !knowledge ? (
        <div className="flex items-center gap-2 py-2 text-cyan-400 text-xs">
          <Disc3 className="w-3.5 h-3.5 animate-spin" />
          <span>正在检索本地资料库…</span>
        </div>
      ) : (
        <div className="space-y-2 pt-1 text-xs">
          <div className="text-slate-300 leading-relaxed text-[11px]">
            {knowledge?.factual_summary || '暂无详细公开背景信息；该艺人可能属于早期独立发行或地下厂牌（不作为排除依据）。'}
          </div>
          {(identity.origin || identity.type || genres.length > 0) && (
            <div className="flex flex-wrap gap-1 pt-0.5">
              {identity.origin && (
                <span className="px-1.5 py-0.5 bg-[#24293c] text-slate-300 rounded text-[10px]">
                  📍 {identity.origin}
                </span>
              )}
              {identity.type && (
                <span className="px-1.5 py-0.5 bg-[#24293c] text-slate-300 rounded text-[10px]">
                  👤 {identity.type}
                </span>
              )}
              {genres.slice(0, 3).map((g: string, i: number) => (
                <span key={i} className="px-1.5 py-0.5 bg-[#1b2130] border border-[#2e374f] text-blue-300 rounded text-[10px]">
                  {g}
                </span>
              ))}
            </div>
          )}
          {knowledge?.sources && knowledge.sources.length > 0 && (
            <div className="pt-1.5 border-t border-[#23283c]">
              <span className="text-[10px] text-slate-500">核验来源 ({knowledge.sources.length}):</span>
              <div className="mt-1 flex flex-col gap-0.5">
                {knowledge.sources.slice(0, 2).map((url, i) => (
                  <a
                    key={i}
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[10px] text-cyan-400 hover:text-cyan-300 truncate flex items-center gap-1"
                  >
                    <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                    <span className="truncate">{url}</span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Inspector({candidate,updateStatus}:{candidate:CandidateItem;updateStatus:(status:ReviewStatus)=>Promise<void>}){
  return <div className="p-4 space-y-4">
    <div className="flex space-x-3 items-start bg-[#1a1d2a] p-3 rounded-lg border border-[#272b3d]"><img src={candidate.cover_url||'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="%2364748b" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>'} className="w-16 h-16 rounded object-cover border border-[#333a4f] shrink-0 bg-slate-900"/><div className="flex-1 min-w-0"><div className="font-bold text-sm text-slate-100 truncate">{candidate.title}</div><div className="text-slate-400 truncate mt-0.5">{candidate.artist_names}</div><div className="flex items-center space-x-2 mt-2"><span className="px-1.5 py-0.5 bg-[#23283a] text-slate-300 rounded font-mono text-[10px]">ID: #{candidate.id}</span><span className={`px-1.5 py-0.5 rounded font-mono text-[10px] status-${candidate.status}`}>{candidate.status}</span></div></div></div>
    <ArtistDrawerCard artistName={candidate.artist_names} songTitle={candidate.title} albumTitle={candidate.album_title} />
    <div className="bg-[#1a1d2a] p-3 rounded-lg border border-[#272b3d] space-y-2"><div className="flex items-center justify-between text-slate-300 font-semibold border-b border-[#282d3f] pb-1.5"><div className="flex items-center space-x-1.5"><Sparkles className="w-3.5 h-3.5 text-blue-400"/><span>机筛分层与置信度特征</span></div><span className="font-mono text-blue-400">完备度: {candidate.completeness_score}/6</span></div><div className="space-y-1.5 pt-1"><div className="flex items-center justify-between"><span className="text-slate-400">机筛分级:</span><span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${candidate.screening_tier==='TIER_1_HOT'?'tier-badge-1':candidate.screening_tier==='TIER_2_RULE'?'tier-badge-2':candidate.screening_tier==='TIER_3_ATTENTION'?'tier-badge-3':'tier-badge-4'}`}>{candidate.screening_tier}</span></div><div className="flex items-start justify-between"><span className="text-slate-400">命中规则:</span><span className="text-slate-200 text-right font-mono max-w-[200px]">{candidate.rule_applied||'默认规则'}</span></div><div className="text-slate-400"><span>特征标签:</span><div className="flex flex-wrap gap-1 mt-1">{candidate.screening_reasons?.map((reason,i)=><span key={i} className="px-1.5 py-0.5 bg-[#24293c] text-slate-300 rounded text-[10px]">{reason}</span>)}</div></div></div></div>
    <div className="bg-[#1a1d2a] p-3 rounded-lg border border-[#272b3d] space-y-2"><div className="text-slate-300 font-semibold border-b border-[#282d3f] pb-1.5 flex items-center justify-between"><span>跨平台多源比对 ({candidate.raw_candidates?.length||1} 源)</span><span className="text-slate-500 font-mono text-[10px]">来源追溯</span></div><div className="space-y-2 pt-1">{candidate.raw_candidates?.map(raw=><div key={raw.id} className="p-2 bg-[#12141d] rounded border border-[#232738] space-y-1"><div className="flex items-center justify-between"><span className={`px-1.5 py-0.5 rounded font-semibold text-[10px] badge-${raw.platform}`}>{raw.platform.toUpperCase()}</span><span className="font-mono text-slate-500 text-[10px]">Source ID: {raw.source_id}</span></div><div className="text-slate-200 font-medium truncate">{raw.title}</div><div className="text-slate-400 text-[11px] truncate">{raw.artist_names} · 《{raw.album_title||'无专辑'}》</div><div className="flex items-center justify-between text-[10px] text-slate-500 pt-1"><span>{raw.release_date||'未知日期'}</span>{raw.source_url&&<a href={raw.source_url} target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300 flex items-center space-x-0.5"><span>原曲直链</span><ExternalLink className="w-2.5 h-2.5"/></a>}</div></div>)}</div></div>
    <div className="grid grid-cols-2 gap-2 pt-1"><button onClick={()=>void updateStatus('approved')} className="flex items-center justify-center space-x-1 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded font-semibold"><CheckCircle2 className="w-4 h-4"/><span>通过此单曲</span></button><button onClick={()=>void updateStatus('rejected')} className="flex items-center justify-center space-x-1 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded font-semibold"><XCircle className="w-4 h-4"/><span>否决此单曲</span></button></div>
  </div>;
}
