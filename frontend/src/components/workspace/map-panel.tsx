import { useEffect, useMemo, useRef, useState } from 'react';
import { LocateFixed, MapPin, Minus, Plus, RefreshCw } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { SearchCandidate } from '@/lib/types';
import { createTravelMap, isValidPosition, type MapPoint, type MapStatus, type TravelMap } from '@/lib/map-loader';

interface MapPanelProps {
  candidates: SearchCandidate[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  mode: 'demo' | 'api';
}

/** 景点地图工作区：底图、编号标记和候选列表共享同一个选中地点。 */
export function MapPanel({ candidates, selectedId, onSelect, mode }: MapPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<TravelMap | null>(null);
  const onSelectRef = useRef(onSelect);
  const selectedRef = useRef(selectedId);
  const [retry, setRetry] = useState(0);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<MapStatus>({ provider: 'osm', state: 'loading', message: '正在加载地图' });
  const points = useMemo<MapPoint[]>(() => candidates.flatMap((candidate, index) =>
    isValidPosition(candidate.longitude, candidate.latitude)
      ? [{ id: candidate.place_id, name: candidate.name, number: index + 1, position: [candidate.longitude, candidate.latitude] }]
      : []), [candidates]);
  const pointsRef = useRef(points);
  const selected = candidates.find(candidate => candidate.place_id === selectedId);
  onSelectRef.current = onSelect;
  selectedRef.current = selectedId;
  pointsRef.current = points;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const controller = new AbortController();
    let adapter: TravelMap | null = null;
    let frame = 0;
    setReady(false);

    // 异步 SDK 可能在卸载后才返回；AbortSignal 阻止旧实例占用新容器。
    void createTravelMap(container, {
      signal: controller.signal,
      onSelect: id => onSelectRef.current(id),
      onStatus: next => { if (!controller.signal.aborted) setStatus(next); },
    }).then(map => {
      if (controller.signal.aborted) { map.destroy(); return; }
      adapter = map;
      mapRef.current = map;
      map.setPoints(pointsRef.current, selectedRef.current);
      setReady(true);
    }).catch(() => {
      if (!controller.signal.aborted) {
        setStatus({ provider: 'osm', state: 'error', message: '地图暂时不可用，请重试' });
      }
    });

    // 列宽调整和移动端页签重新展示均需通知地图重算容器大小。
    const observer = new ResizeObserver(entries => {
      if (!entries.some(entry => entry.contentRect.width > 0 && entry.contentRect.height > 0)) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => adapter?.resize());
    });
    observer.observe(container);
    return () => {
      controller.abort();
      observer.disconnect();
      cancelAnimationFrame(frame);
      adapter?.destroy();
      if (mapRef.current === adapter) mapRef.current = null;
    };
  }, [retry]);

  useEffect(() => { mapRef.current?.setPoints(points, selectedRef.current); }, [points]);
  useEffect(() => { mapRef.current?.select(selectedId); }, [selectedId]);

  return (
    <section className="map-panel" aria-label="景点地图">
      <header className="map-header">
        <div><MapPin size={16} aria-hidden="true" /><h2>目的地地图</h2></div>
        <span>{points.length} 个地点</span>
      </header>
      <div className="map-surface">
        <div ref={containerRef} className="map-canvas" aria-label="可交互地图" />
        <div className="map-source-badge">{status.provider === 'amap' ? '高德地图' : 'OpenStreetMap'}</div>
        <div className="map-toolbar" role="group" aria-label="地图视野">
          <Tooltip><TooltipTrigger asChild><button className="icon-button map-control" type="button" aria-label="显示全部地点" disabled={!ready || points.length === 0} onClick={() => mapRef.current?.fitAll()}><LocateFixed size={18} /></button></TooltipTrigger><TooltipContent>显示全部地点</TooltipContent></Tooltip>
          <Tooltip><TooltipTrigger asChild><button className="icon-button map-control" type="button" aria-label="放大地图" disabled={!ready} onClick={() => mapRef.current?.zoomBy(1)}><Plus size={18} /></button></TooltipTrigger><TooltipContent>放大地图</TooltipContent></Tooltip>
          <Tooltip><TooltipTrigger asChild><button className="icon-button map-control" type="button" aria-label="缩小地图" disabled={!ready} onClick={() => mapRef.current?.zoomBy(-1)}><Minus size={18} /></button></TooltipTrigger><TooltipContent>缩小地图</TooltipContent></Tooltip>
        </div>
        {status.message && (
          <div className={`map-status ${status.state === 'error' ? 'is-error' : ''}`} role="status">
            <span>{status.message}</span>
            {status.state === 'error' && <Tooltip><TooltipTrigger asChild><button className="icon-button" type="button" aria-label="重新加载地图" onClick={() => setRetry(value => value + 1)}><RefreshCw size={15} /></button></TooltipTrigger><TooltipContent>重新加载地图</TooltipContent></Tooltip>}
          </div>
        )}
        {selected ? (
          <div className="map-selection">
            <span className="map-selection-icon"><MapPin size={19} aria-hidden="true" /></span>
            <div><strong>{selected.name}</strong><p>{selected.address || selected.city}</p></div>
          </div>
        ) : points.length === 0 ? (
          <div className="map-selection map-empty"><MapPin size={19} aria-hidden="true" /><span>暂无可定位的地点</span></div>
        ) : null}
        <div className="map-attribution">{mode === 'demo' ? '地点数据：演示样例' : '地点数据：高德查询'}{candidates.length > points.length ? ` · ${candidates.length - points.length} 个地点缺少有效坐标` : ''}</div>
      </div>
    </section>
  );
}
