<script setup lang="ts">
defineProps<{ title?: string }>()
</script>

<template>
  <div style="margin-bottom: 14px">
    <div class="strong" style="font-size: 12px; color: var(--muted); margin-bottom: 4px">{{ title }}</div>
    <slot />
  </div>
</template>
