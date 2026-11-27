<script setup>
import { computed, useAttrs } from "vue";
import { cn } from "../../lib/cn";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  variant: { type: String, default: "default" }, // default | primary | ghost | danger
  size: { type: String, default: "md" },         // sm | md
});
const attrs = useAttrs();

const VARIANTS = {
  default: "border-[var(--border)] bg-[var(--field)] text-[color:var(--ink)] hover:bg-[var(--field-hover)]",
  primary: "border-transparent bg-[var(--accent)] text-white hover:bg-[var(--accent-strong)]",
  ghost: "border-transparent bg-transparent text-[color:var(--ink)] hover:bg-[var(--field-hover)]",
  danger: "border-transparent bg-[var(--bad)] text-white hover:opacity-90",
};
const SIZES = {
  sm: "h-7 px-2 gap-1",
  md: "h-8 px-3 gap-1.5",
};

const classes = computed(() => cn(
  "inline-flex items-center justify-center whitespace-nowrap rounded-[var(--r-sm)] border",
  "text-[length:var(--fs-ui)] font-medium transition-colors",
  "disabled:cursor-not-allowed disabled:opacity-50",
  VARIANTS[props.variant] || VARIANTS.default,
  SIZES[props.size] || SIZES.md,
  attrs.class,
));
</script>

<template>
  <button v-bind="attrs" :class="classes">
    <slot />
  </button>
</template>
