<script lang="ts">
  import Confetti from '../lib/Confetti.svelte'
</script>

<div class="starting">
  <Confetti opacity={0.85} />
  <p class="helper">Starting up...</p>
</div>

<style>
  .starting {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
</style>
