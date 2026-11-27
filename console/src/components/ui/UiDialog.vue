<script setup>
import { onBeforeUnmount, onMounted } from "vue";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: "" },
  width: { type: String, default: "520px" },
});
const emit = defineEmits(["update:modelValue"]);

function close() {
  emit("update:modelValue", false);
}
function onKey(e) {
  if (e.key === "Escape" && props.modelValue) close();
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue"
         class="fixed inset-0 z-[90] flex items-center justify-center bg-black/35 p-4"
         @click.self="close">
      <div class="w-full overflow-hidden rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--card)] shadow-[var(--shadow)]"
           :style="{ maxWidth: width }">
        <div class="flex items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-3">
          <h3 class="text-[length:var(--fs-h3)] font-semibold text-[color:var(--ink)]">{{ title }}</h3>
          <button class="rounded-[var(--r-sm)] px-2 py-1 text-[18px] leading-none text-[color:var(--muted)] hover:bg-[var(--field-hover)]"
                  title="关闭" @click="close">×</button>
        </div>
        <div class="px-4 py-3 text-[length:var(--fs-ui)] text-[color:var(--ink)]">
          <slot />
        </div>
        <div v-if="$slots.footer"
             class="flex justify-end gap-2 border-t border-[var(--border)] bg-[var(--field-inset)] px-4 py-3">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
