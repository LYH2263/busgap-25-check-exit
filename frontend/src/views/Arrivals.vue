<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
onMounted(async () => { rows.value = await api('/arrivals') })
</script>
<template>
  <h1>到站</h1>
  <p class="sub">各班次实际到站记录</p>
  <div class="card">
    <table>
      <thead><tr><th>班次</th><th>站序</th><th>站点</th><th>实际到站</th><th>状态</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id ?? JSON.stringify(r)" :class="{ 'row-cancelled': r.cancelled }">
          <td>{{ r.trip_no }}</td><td>{{ r.stop_seq }}</td><td>{{ r.stop_name }}</td><td>{{ r.actual_arrive }}</td>
          <td><span v-if="r.cancelled" class="badge badge-muted">已取消</span><span v-else class="muted">正常</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
