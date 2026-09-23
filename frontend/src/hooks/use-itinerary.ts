import { useEffect, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { demo } from '@/lib/demo';
import { officialVersionNo, versionFromRun } from '@/lib/versions';
import type { RunView, SessionView, VersionSummary } from '@/lib/types';

export function useItinerary(session: SessionView | null, runs: RunView[], mode: 'api' | 'demo') {
  const client = useQueryClient();
  const [selection, setSelection] = useState<{ no: number; official: number | null } | null>(null);
  const currentNo = officialVersionNo(session, runs);
  const savedRun = [...runs].reverse().find(run => run.result.saved_version);
  const tripId = session?.trip_id || savedRun?.result.saved_version?.trip_id;
  // 新正式版本到达时自动展示它；单纯刷新不会打断历史版本查看。
  const selectedNo = selection?.official === currentNo ? selection.no : currentNo;
  const embedded = runs.map(versionFromRun).find(version => version?.version_no === selectedNo);
  const key = ['versions', mode, tripId];
  const history = useInfiniteQuery({
    queryKey: key, enabled: !!tripId, initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => mode === 'demo' ? demo.getVersions(session!.session_id, pageParam) : api.getVersions(tripId!, pageParam, signal),
    getNextPageParam: page => page.next_cursor,
  });
  const detail = useQuery({
    queryKey: ['version', mode, tripId, selectedNo], enabled: !!tripId && selectedNo !== null && !embedded,
    queryFn: ({ signal }) => mode === 'demo' ? demo.getVersion(session!.session_id, selectedNo!) : api.getVersion(tripId!, selectedNo!, signal),
  });
  useEffect(() => { if (tripId) void client.invalidateQueries({ queryKey: ['versions', mode, tripId] }); }, [client, mode, tripId, savedRun?.run_id]);
  const versions = new Map<number, VersionSummary>();
  for (const version of history.data?.pages.flatMap(page => page.items) ?? []) versions.set(version.version_no, version);
  for (const run of runs) {
    const version = versionFromRun(run);
    if (version) versions.set(version.version_no, { ...version, summary: version.itinerary.summary });
  }
  return {
    version: embedded || detail.data || null, currentNo, selectedNo,
    currentRequest: runs.map(versionFromRun).find(version => version?.version_no === currentNo)?.trip_request || (selectedNo === currentNo ? detail.data?.trip_request : undefined),
    versions: [...versions.values()].sort((a, b) => b.version_no - a.version_no),
    select: (no: number | null) => setSelection(no === null ? null : { no, official: currentNo }),
    error: detail.error || history.error,
    loading: !!tripId && selectedNo !== null && !embedded && detail.isPending,
    hasMore: !!history.hasNextPage,
    loadMore: () => { if (!history.isFetchingNextPage) void history.fetchNextPage(); },
    retry: () => { if (tripId) { if (selectedNo !== null) void detail.refetch(); void history.refetch(); } },
  };
}
