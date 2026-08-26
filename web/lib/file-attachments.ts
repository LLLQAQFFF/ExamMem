"use client";

export async function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function extractBase64FromDataUrl(dataUrl: string): string {
  return dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
}

export function titleFromFilename(filename: string): string {
  const trimmed = filename.trim();
  const extensionStart = trimmed.lastIndexOf(".");
  if (extensionStart <= 0) return trimmed;
  return trimmed.slice(0, extensionStart).trim() || trimmed;
}

export function autofillTitleFromFilename(
  currentTitle: string,
  currentIsAutomatic: boolean,
  filename: string,
): { title: string; isAutomatic: boolean } {
  if (currentTitle.trim() && !currentIsAutomatic) {
    return { title: currentTitle, isAutomatic: false };
  }
  return { title: titleFromFilename(filename), isAutomatic: true };
}
