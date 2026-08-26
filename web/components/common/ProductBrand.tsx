import Image from "next/image";

interface ProductBrandProps {
  className?: string;
  compact?: boolean;
  iconSize?: number;
  wordmarkClassName?: string;
}

export function ProductBrand({
  className = "",
  compact = false,
  iconSize = 22,
  wordmarkClassName = "text-[18px]",
}: ProductBrandProps) {
  return (
    <span
      aria-label="ExamMem"
      className={`inline-flex items-center gap-1.5 ${className}`}
    >
      <Image
        src="/exammem-mark.png"
        alt=""
        width={iconSize}
        height={iconSize}
        className="shrink-0 select-none"
      />
      {!compact && (
        <span
          className={`bg-gradient-to-r from-[#06337f] via-[#078ff0] to-[#7138ff] bg-clip-text font-semibold leading-none tracking-[-0.035em] text-transparent dark:from-[#89dcff] dark:via-[#2ca8ff] dark:to-[#9b66ff] ${wordmarkClassName}`}
        >
          ExamMem
        </span>
      )}
    </span>
  );
}
