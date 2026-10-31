<script setup>
/**
 * 内置手册（2026-10-31）。
 *
 * ## 为什么要它
 *
 * 用户的意见：“本来这些东西都应该写在手册里持久化备查的。”
 * 他说得对——此前的"说明"只有两处：首启五步引导（关掉就没了），和散落在各个面板上的
 * 一句话提示。判据怎么算、改坏了怎么退、升级会不会覆盖我的东西，全靠口头解释，
 * 下次还得再问一遍。
 *
 * 手册内容随包发布（`src/help/*.md`，vite 以 raw 方式打进 bundle），
 * 因此**离线可查、不依赖网络、不依赖工作区**。改手册 = 改 md 文件 + 重新打包。
 *
 * ## 为什么不读工作区的 docs/
 *
 * 那些是给开发者看的（构建步骤、架构说明）。作者要的是一份 "我该点哪儿" 的说明，
 * 两份东西不能混。仓库里的 `docs/` 继续给开发用，`src/help/` 专门给作者。
 */
import { ref, computed, onMounted } from "vue";
import { renderMarkdown, mdTitle } from "./md.js";

// 手册源：文件名前缀决定顺序（01-、02-…），标题从正文第一行 `# ` 取
const modules = import.meta.glob("./help/*.md", { query: "?raw", import: "default", eager: true });

const docs = Object.keys(modules)
  .sort()
  .map((path) => {
    const src = modules[path] || "";
    return {
      id: path.replace(/^\.\/help\//, "").replace(/\.md$/, ""),
      title: mdTitle(src) || path,
      html: renderMarkdown(src),
      raw: src,
    };
  });

const activeId = ref("");
const query = ref("");

const current = computed(
  () => docs.find((d) => d.id === activeId.value) || docs[0] || null);

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return docs;
  // 搜索范围 = 标题 + 正文（正文用 raw，用户搜"计入"能搜到手册里的解释）
  return docs.filter(
    (d) => d.title.toLowerCase().includes(q) || d.raw.toLowerCase().includes(q));
});

onMounted(() => {
  if (docs.length) activeId.value = docs[0].id;
});
</script>

<template>
  <div class="help-wrap">
    <div class="help-side">
      <div class="help-side-head">
        <b>使用手册</b>
        <span class="meta">{{ docs.length }} 篇 · 随包附带，离线可查</span>
      </div>
      <input v-model="query" class="help-search" placeholder="搜关键词（如：不计入 / 还原 / 上限）" />
      <div class="help-toc">
        <button v-for="d in filtered" :key="d.id" class="help-toc-item"
                :class="{ on: current && d.id === current.id }"
                @click="activeId = d.id">{{ d.title }}</button>
        <div v-if="!filtered.length" class="meta" style="padding: 8px 2px;">
          没搜到。换个词试试，或清空搜索看全部。
        </div>
      </div>
    </div>
    <div class="help-main">
      <div v-if="!docs.length" class="empty">
        手册没有随包打进来（src/help/*.md 缺失）—— 这是打包问题，请报告。
      </div>
      <article v-else class="help-article" v-html="current.html"></article>
    </div>
  </div>
</template>
