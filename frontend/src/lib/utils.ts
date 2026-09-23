import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** 合并条件样式，并让后传入的 Tailwind 工具类覆盖冲突项。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
