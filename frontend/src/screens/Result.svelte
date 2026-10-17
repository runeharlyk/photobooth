<script lang="ts">
  import Confetti from '../lib/Confetti.svelte'
  import Sticker from '../lib/Sticker.svelte'
  import { deleteCapture, newPhoto, type BoothState } from '../lib/booth'

  let { state }: { state: BoothState } = $props()

  const media = $derived(`/media/${state.capture_id}`)
</script>

<div class="result">
  <Confetti opacity={0.5} />
  <div class="print">
    <img src="{media}/photo.jpg" alt="" />
    {#if state.boomerang_ready}
      <video src="{media}/boomerang.mp4" autoplay loop muted playsinline></video>
    {/if}
  </div>
  <div class="side">
    <div class="scan"><Sticker tilt={-5} size={22}>SCAN TO GET YOUR PHOTO</Sticker></div>
    <img class="qr" src="/api/captures/{state.capture_id}/qr.svg" alt="" />
    <div class="actions">
      {#if state.delete_available}
        <button class="delete" onclick={deleteCapture}>Delete</button>
      {/if}
      <button class="new" onclick={newPhoto}>New photo</button>
    </div>
  </div>
</div>

<style>
  .result {
    position: absolute;
    inset: 0;
  }

  .print {
    position: absolute;
    left: 4%;
    top: 50%;
    width: 56%;
    display: grid;
    translate: 0 -50%;
    rotate: -2deg;
    border: 8px solid #fff;
    background: #000;
    box-shadow: 8px 10px 0 #0009;
  }

  .print > * {
    grid-area: 1 / 1;
    display: block;
    width: 100%;
    aspect-ratio: 16 / 9;
    object-fit: cover;
  }

  .side {
    position: absolute;
    right: 4%;
    top: 0;
    bottom: 0;
    width: 30%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 22px;
  }

  .scan {
    align-self: center;
    margin-bottom: -34px;
    z-index: 1;
  }

  .qr {
    display: block;
    width: 100%;
    aspect-ratio: 1;
    border: 8px solid #fff;
    background: #fff;
  }

  .actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }

  button {
    grid-column: 2;
    padding: 16px 0;
    border-radius: 999px;
    font-size: 22px;
    font-weight: 800;
    transition: transform 80ms ease-out;
  }

  button:active {
    transform: scale(0.95);
  }

  .delete {
    grid-column: 1;
    border: 2px solid #555;
    color: var(--muted);
  }

  .new {
    background: var(--green);
    box-shadow: 0 0 18px #2bd96b55;
    color: #000;
  }
</style>
