import type { ItineraryVersion, RunView, SessionView } from './types';

/** 只有完成且有保存回执的结果，才能直接提升为正式版本。 */
export function versionFromRun(run: RunView): ItineraryVersion | null {
  const { saved_version, trip_request, itinerary, routes, validation } = run.result;
  if (run.status !== 'completed' || !saved_version || !trip_request || !itinerary || !routes || !validation) return null;
  return { ...saved_version, source_run_id: run.run_id, trip_request, itinerary, routes, validation, created_at: run.finished_at || run.created_at };
}

export function officialVersionNo(session: SessionView | null, runs: RunView[]): number | null {
  const completed = runs.filter(run => run.status === 'completed' && run.result.saved_version)
    .map(run => run.result.saved_version!.version_no);
  let pointer = session?.current_version_no ?? null;
  // 保存后失败的版本需要明确展示入口，但不能因会话指针刷新而自动替换旧版本。
  const failedSave = runs.find(run => run.status === 'failed' && run.result.saved_version?.version_no === pointer);
  if (failedSave) pointer = failedSave.base_version_no;
  const known = [...completed, ...(pointer === null ? [] : [pointer])];
  return known.length ? Math.max(...known) : null;
}
