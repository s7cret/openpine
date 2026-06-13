export type DataSummaryLoadState<T> = {
  summary: T | null
  error: string
}

export async function loadDataSummaryState<T>(
  previousSummary: T | null,
  fetchSummary: () => Promise<T>,
): Promise<DataSummaryLoadState<T>> {
  try {
    return { summary: await fetchSummary(), error: '' }
  } catch (error: any) {
    const message = error?.response?.data?.detail ?? error?.message ?? 'Failed to load market data summary'
    return { summary: previousSummary, error: typeof message === 'string' ? message : JSON.stringify(message) }
  }
}
