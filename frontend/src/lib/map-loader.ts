import AMapLoader from '@amap/amap-jsapi-loader';
import gcoord from 'gcoord';
import 'leaflet/dist/leaflet.css';

/** 地图只接收已校验的 GCJ-02 坐标，避免展示层修改接口原始数据。 */
export interface MapPoint {
  id: string;
  name: string;
  number: number;
  position: [number, number];
}

export interface MapStatus {
  provider: 'amap' | 'osm';
  state: 'loading' | 'ready' | 'error';
  message: string | null;
}

/** 统一地图生命周期和交互，组件无需了解各地图 SDK 的坐标与事件差异。 */
export interface TravelMap {
  setPoints(points: MapPoint[], selectedId: string | null): void;
  select(id: string | null): void;
  fitAll(): void;
  zoomBy(delta: number): void;
  resize(): void;
  destroy(): void;
}

interface MapOptions {
  signal: AbortSignal;
  onSelect: (id: string) => void;
  onStatus: (status: MapStatus) => void;
}

interface AMapMarker {
  setMap(map: AMapInstance | null): void;
  setzIndex(value: number): void;
}

interface AMapInstance {
  on(event: string, callback: () => void): void;
  off(event: string, callback: () => void): void;
  destroy(): void;
  setFitView(markers: AMapMarker[], immediately: boolean, padding: number[], maxZoom: number): void;
  setCenter(position: [number, number], immediately?: boolean): void;
  getZoom(): number;
  setZoom(zoom: number): void;
  resize?(): void;
}

interface AMapSdk {
  Map: new (container: HTMLElement, options: Record<string, unknown>) => AMapInstance;
  Marker: new (options: Record<string, unknown>) => AMapMarker;
}

type SecurityWindow = Window & {
  _AMapSecurityConfig?: { serviceHost?: string; securityJsCode?: string };
};

const CHANGSHA_CENTER: [number, number] = [112.9388, 28.2282];
const LOAD_TIMEOUT = 12_000;
let sdkPromise: Promise<AMapSdk> | null = null;

/** 检查有限数值与经纬度范围，坏数据不会进入地图 SDK。 */
export function isValidPosition(longitude: number, latitude: number): boolean {
  return Number.isFinite(longitude) && Number.isFinite(latitude)
    && Math.abs(longitude) <= 180 && Math.abs(latitude) <= 90;
}

/** Leaflet 使用 WGS-84；必须转换高德的 GCJ-02，不能直接交换经纬度使用。 */
function leafletPosition(position: [number, number]): [number, number] {
  const [longitude, latitude] = gcoord.transform(position, gcoord.GCJ02, gcoord.WGS84);
  return [latitude, longitude];
}

/** 用 DOM 文本承载标记，景点名称不进入 HTML 模板。 */
function markerNode(point: MapPoint, selected: boolean): HTMLDivElement {
  const element = document.createElement('div');
  element.className = 'map-marker';
  element.textContent = String(point.number);
  element.dataset.selected = String(selected);
  element.title = point.name;
  return element;
}

function aborted(): DOMException {
  return new DOMException('地图初始化已取消', 'AbortError');
}

/** 限制外部 SDK 等待时间；StrictMode 卸载后及时结束当前初始化。 */
function waitFor<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(aborted()); return; }
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', cancel);
    };
    const cancel = () => { cleanup(); reject(aborted()); };
    const timer = window.setTimeout(() => {
      cleanup();
      reject(new Error('地图服务加载超时'));
    }, LOAD_TIMEOUT);
    signal.addEventListener('abort', cancel, { once: true });
    promise.then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
  });
}

function loadAmapSdk(): Promise<AMapSdk> {
  if (sdkPromise) return sdkPromise;
  const serviceHost = import.meta.env.VITE_AMAP_SECURITY_PROXY?.trim();
  const securityJsCode = import.meta.env.DEV
    ? import.meta.env.VITE_AMAP_SECURITY_CODE?.trim() : undefined;

  // 生产环境只接受服务端代理地址，浏览器安全码仅用于本地开发。
  (window as SecurityWindow)._AMapSecurityConfig = serviceHost
    ? { serviceHost } : securityJsCode ? { securityJsCode } : {};
  sdkPromise = AMapLoader.load({
    key: import.meta.env.VITE_AMAP_JS_KEY?.trim() ?? '',
    version: '2.0',
  }).then(sdk => sdk as AMapSdk).catch((error: unknown) => {
    sdkPromise = null;
    throw error;
  });
  return sdkPromise;
}

async function createAmap(container: HTMLElement, options: MapOptions): Promise<TravelMap> {
  options.onStatus({ provider: 'amap', state: 'loading', message: '正在加载高德地图' });
  const sdk = await waitFor(loadAmapSdk(), options.signal);
  if (options.signal.aborted) throw aborted();
  const map = new sdk.Map(container, {
    center: CHANGSHA_CENTER, zoom: 12, viewMode: '2D', resizeEnable: true,
    showIndoorMap: false,
  });
  let complete: (() => void) | undefined;
  try {
    await waitFor(new Promise<void>(resolve => {
      complete = resolve;
      map.on('complete', complete);
    }), options.signal);
  } catch (error) {
    map.destroy();
    throw error;
  } finally {
    if (complete) map.off('complete', complete);
  }
  options.onStatus({ provider: 'amap', state: 'ready', message: null });

  let entries: Array<{ point: MapPoint; marker: AMapMarker; element: HTMLDivElement }> = [];
  let fitPending = false;
  let pendingSelection: string | null = null;
  const hasViewport = () => container.clientWidth > 0 && container.clientHeight > 0;
  const clear = () => {
    entries.forEach(entry => entry.marker.setMap(null));
    entries = [];
  };
  const fitAll = () => {
    if (!entries.length) { fitPending = false; return; }
    // 页签尚未展示时不让 SDK 按零尺寸计算视野，首次可见时再补一次。
    if (!hasViewport()) { fitPending = true; return; }
    fitPending = false;
    map.setFitView(entries.map(entry => entry.marker), true, [72, 48, 120, 48], 15);
  };
  const select = (id: string | null) => {
    pendingSelection = null;
    entries.forEach(entry => {
      const selected = entry.point.id === id;
      entry.element.dataset.selected = String(selected);
      entry.marker.setzIndex(selected ? 200 : 100);
      if (selected) {
        if (hasViewport()) map.setCenter(entry.point.position, true);
        else pendingSelection = id;
      }
    });
  };
  return {
    setPoints(points, selectedId) {
      clear();
      entries = points.map(point => {
        const element = markerNode(point, point.id === selectedId);
        element.setAttribute('role', 'button');
        element.setAttribute('aria-label', `${point.number}. ${point.name}`);
        element.tabIndex = 0;
        element.onclick = () => options.onSelect(point.id);
        element.onkeydown = event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            options.onSelect(point.id);
          }
        };
        const marker = new sdk.Marker({
          map, position: point.position, content: element,
          anchor: 'center', title: point.name, zIndex: point.id === selectedId ? 200 : 100,
        });
        return { point, marker, element };
      });
      fitAll();
      if (fitPending) pendingSelection = selectedId;
    },
    select,
    fitAll,
    zoomBy: delta => map.setZoom(Math.min(18, Math.max(3, map.getZoom() + delta))),
    resize() {
      if (map.resize) map.resize();
      else window.dispatchEvent(new Event('resize'));
      if (fitPending) fitAll();
      if (pendingSelection && hasViewport()) select(pendingSelection);
    },
    destroy() { clear(); map.destroy(); },
  };
}

async function createOsm(container: HTMLElement, options: MapOptions, reason: string): Promise<TravelMap> {
  options.onStatus({ provider: 'osm', state: 'loading', message: '正在加载 OpenStreetMap' });
  const leaflet = await import('leaflet');
  if (options.signal.aborted) throw aborted();
  const map = leaflet.map(container, {
    center: leafletPosition(CHANGSHA_CENTER), zoom: 12,
    zoomControl: false, attributionControl: true, scrollWheelZoom: true,
  });
  const tiles = leaflet.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors',
  });
  let tileLoaded = false;
  let failedTiles = 0;
  let destroyed = false;
  const update = (state: MapStatus['state'], message: string) => {
    if (!destroyed && !options.signal.aborted) options.onStatus({ provider: 'osm', state, message });
  };
  const timer = window.setTimeout(() => {
    if (!tileLoaded) update('error', '底图加载超时，请检查网络后重试');
  }, LOAD_TIMEOUT);
  tiles.on('tileload', () => {
    if (!tileLoaded) {
      tileLoaded = true;
      clearTimeout(timer);
      update('ready', reason);
    }
  });
  tiles.on('loading', () => { failedTiles = 0; });
  tiles.on('tileerror', () => {
    failedTiles += 1;
    update('error', tileLoaded ? '部分地图区域加载失败，请重试' : '底图暂时不可用，请检查网络后重试');
  });
  tiles.on('load', () => {
    if (failedTiles === 0 && tileLoaded) update('ready', reason);
  });
  tiles.addTo(map);
  const markers = leaflet.layerGroup().addTo(map);
  let entries: Array<{ point: MapPoint; marker: import('leaflet').Marker; element: HTMLDivElement }> = [];
  let fitPending = false;
  let pendingSelection: string | null = null;
  const hasViewport = () => container.clientWidth > 0 && container.clientHeight > 0;
  const fitAll = () => {
    if (!entries.length) { fitPending = false; return; }
    // 隐藏容器减去留白后会产生负缩放比例；先记录待定位，在 resize 恢复。
    if (!hasViewport()) { fitPending = true; return; }
    fitPending = false;
    map.invalidateSize({ pan: false, animate: false });
    const size = map.getSize();
    const horizontal = Math.min(48, size.x / 4);
    map.fitBounds(leaflet.latLngBounds(entries.map(entry => entry.marker.getLatLng())), {
      paddingTopLeft: [horizontal, Math.min(70, size.y / 4)],
      paddingBottomRight: [horizontal, Math.min(112, size.y / 3)],
      maxZoom: 15, animate: false,
    });
  };
  const select = (id: string | null) => {
    pendingSelection = null;
    entries.forEach(entry => {
      const selected = entry.point.id === id;
      entry.element.dataset.selected = String(selected);
      entry.marker.setZIndexOffset(selected ? 200 : 0);
      if (selected) {
        if (hasViewport()) map.panTo(entry.marker.getLatLng(), { animate: false });
        else pendingSelection = id;
      }
    });
  };
  return {
    setPoints(points, selectedId) {
      markers.clearLayers();
      entries = points.map(point => {
        const element = markerNode(point, point.id === selectedId);
        const marker = leaflet.marker(leafletPosition(point.position), {
          title: point.name, alt: `${point.number}. ${point.name}`, keyboard: true,
          zIndexOffset: point.id === selectedId ? 200 : 0,
          icon: leaflet.divIcon({ html: element, className: 'map-marker-wrap', iconSize: [34, 34], iconAnchor: [17, 17] }),
        }).on('click', () => options.onSelect(point.id)).addTo(markers);
        marker.getElement()?.setAttribute('aria-label', `${point.number}. ${point.name}`);
        return { point, marker, element };
      });
      fitAll();
      if (fitPending) pendingSelection = selectedId;
    },
    select,
    fitAll,
    zoomBy: delta => map.setZoom(Math.min(18, Math.max(3, map.getZoom() + delta)), { animate: false }),
    resize() {
      map.invalidateSize({ pan: false, animate: false });
      // 只有隐藏期间积压的定位操作需要恢复；普通缩放布局不重置用户视野。
      if (fitPending) fitAll();
      if (pendingSelection && hasViewport()) select(pendingSelection);
    },
    destroy() {
      destroyed = true;
      clearTimeout(timer);
      tiles.off();
      entries.forEach(entry => entry.marker.off());
      map.remove();
    },
  };
}

/** 优先高德；缺少浏览器配置或 SDK 加载失败时使用真实 OSM 底图。 */
export async function createTravelMap(container: HTMLElement, options: MapOptions): Promise<TravelMap> {
  const key = import.meta.env.VITE_AMAP_JS_KEY?.trim();
  if (key) {
    try {
      return await createAmap(container, options);
    } catch (error) {
      if (options.signal.aborted) throw error;
      container.replaceChildren();
      return createOsm(container, options, '高德暂时不可用，已切换 OpenStreetMap');
    }
  }
  return createOsm(container, options, '高德地图未配置 · OpenStreetMap 底图');
}
