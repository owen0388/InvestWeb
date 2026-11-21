<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    desc?: string
    dense?: boolean
  }>(),
  { title: '', desc: '', dense: false },
)
</script>

<template>
  <section class="card">
    <div v-if="title" class="card-head">
      <div>
        <div class="card-title">{{ title }}</div>
        <div v-if="desc" class="section-desc">{{ desc }}</div>
      </div>
      <slot name="action" />
    </div>
    <div class="card-body" :class="{ tight: dense }">
      <slot />
    </div>
    <div v-if="$slots.footer" class="card-foot">
      <slot name="footer" />
    </div>
  </section>
</template>
