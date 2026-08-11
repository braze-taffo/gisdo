import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function shortPath(path: string, max = 52) {
  if (path.length <= max) return path;
  const head = Math.floor(max * 0.42);
  return `${path.slice(0, head)}…${path.slice(-(max - head - 1))}`;
}

export function newId() {
  return crypto.randomUUID();
}

