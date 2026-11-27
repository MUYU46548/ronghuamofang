import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn-vue 同款 className 合并：条件类 + 去重（后者覆盖前者）。 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
