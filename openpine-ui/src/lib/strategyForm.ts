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
