<template>
  <div class="flex flex-col items-center justify-center gap-2 rounded-[var(--r-lg)] border border-dashed border-[var(--border)] bg-[var(--field-inset)] px-6 py-8 text-center">
    <div class="text-[22px] leading-none text-[color:var(--muted)] opacity-70">
      <slot name="icon">◇</slot>
    </div>
    <div class="text-[length:var(--fs-ui)] leading-[var(--lh-body)] text-[color:var(--muted)]">
      <slot />
    </div>
  </div>
</template>
