export type StrategyFormDraft = {
  name: string
  pine_id: string
  artifact_id: string
  symbol: string
  timeframe: string
  exchange: string
  market_type: string
  params_json: string
  mode: string
}

export type SymbolOption = { symbol: string; baseAsset?: string; quoteAsset?: string }

export type SymbolSearchResult<T extends SymbolOption = SymbolOption> = {
  symbols: T[]
  error: string
}

export function newStrategyForm(
  defaultTimeframe = '1h',
  exchange = 'binance',
  marketType = 'spot',
): StrategyFormDraft {
  return {
    name: '',
    pine_id: '',
    artifact_id: '',
    symbol: '',
    timeframe: defaultTimeframe || '1h',
    exchange: exchange || 'binance',
    market_type: marketType || 'spot',
    params_json: '{}',
    mode: 'paper',
  }
}

export async function loadStrategySymbolOptions<T extends SymbolOption>(
  query: string,
  exchange: string,
  marketType: string,
  search: (query: string, exchange: string, marketType: string) => Promise<T[]>,
): Promise<SymbolSearchResult<T>> {
  try {
    return { symbols: await search(query, exchange, marketType), error: '' }
  } catch (error: any) {
    return { symbols: [], error: error?.response?.data?.detail ?? error?.message ?? 'Symbol search failed' }
  }
}

export function selectStrategySymbol(form: StrategyFormDraft, option: SymbolOption): string {
  form.symbol = option.symbol
  return option.symbol
}

export function clearStrategySymbolForMarketChange(form: StrategyFormDraft): string {
  form.symbol = ''
  return ''
}

export function strategyValidationMessage(form: StrategyFormDraft): string {
  const missingNameOrSymbol = !form.name || !form.symbol
  const missingCompiledSource = !form.pine_id || !form.artifact_id

  if (missingNameOrSymbol && missingCompiledSource) return '❌ Fill required fields: name and symbol. No compiled Pine source is available.'
  if (missingNameOrSymbol) return '❌ Fill required fields: name and symbol.'
  if (missingCompiledSource) return '❌ Select a compiled Pine source before creating the strategy.'
  return ''
}

/**
 * Whether the "Create strategy" button should be disabled.
 *
 * Mirrors `strategyValidationMessage` so the button state and the error
 * banner below it always agree.  A strategy MUST have:
 *   - a non-empty name
 *   - a non-empty symbol
 *   - a Pine source the user explicitly chose (pine_id)
 *   - a compiled artifact for that Pine source (artifact_id)
 *
 * Auto-filling pine_id/artifact_id from the store on submit was tempting
 * but made the form silently create strategies with a random Pine file,
 * which users perceived as "I created a strategy without picking a Pine".
 * The current contract: the form is disabled until the user has chosen
 * both a Pine source and (transitively, via the artifact chip) an artifact.
 */
export function isCreateDisabled(form: StrategyFormDraft, isLoading = false): boolean {
  if (isLoading) return true
  return strategyValidationMessage(form) !== ''
}
