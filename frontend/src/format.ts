// Ingredient quantities arrive as decimal strings. These only tidy how they read
// (3500.000 as 3,500, 12.500 as 12.5) and work on the text, so no digit is ever rounded.
export function readableQuantity(value: string): string {
  const unsigned = value.replace(/^-/, "");
  const [whole, decimals = ""] = unsigned.split(".");
  const fraction = decimals.replace(/0+$/, "");
  const sign = value.startsWith("-") && /[1-9]/.test(unsigned) ? "−" : "";
  return `${sign}${whole.replace(/\B(?=(\d{3})+$)/g, ",")}${fraction && `.${fraction}`}`;
}

export const readableAmount = (value: string, unit: string) => `${readableQuantity(value)} ${unit}`;
