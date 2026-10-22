export const TIME_STEPS = ['5分钟', '15分钟', '30分钟', '1小时', '6小时', '12小时', '1天', '1周', '1个月'];
export function normalizeStep(value: string) { return value.trim().replace(/^(\d+)日$/, '$1天'); }
export function stepOptions(value: string) { const normalized = normalizeStep(value); return TIME_STEPS.includes(normalized) || !normalized ? TIME_STEPS : [...TIME_STEPS, normalized]; }
