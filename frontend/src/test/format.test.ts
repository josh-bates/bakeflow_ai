import { expect, it } from "vitest";
import { readableAmount, readableQuantity } from "../format";

it.each([
  ["3500.000", "3,500"],
  ["450.000", "450"],
  ["12.500", "12.5"],
  ["12.345", "12.345"],
  ["-200.000", "−200"],
  ["-1234.500", "−1,234.5"],
  ["0.000", "0"],
  ["-0.000", "0"],
  ["0.005", "0.005"],
  ["100.000", "100"],
  ["1000", "1,000"],
  ["123456789.120", "123,456,789.12"],
])("reads %s as %s without rounding", (value, expected) => {
  expect(readableQuantity(value)).toBe(expected);
});

it("keeps the unit after a space", () => {
  expect(readableAmount("450.000", "g")).toBe("450 g");
  expect(readableAmount("30.000", "each")).toBe("30 each");
});
