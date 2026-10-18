<script lang="ts">
  import Confetti from '../lib/Confetti.svelte'
  import Sticker from '../lib/Sticker.svelte'
  import { deleteCapture, newPhoto, type BoothState } from '../lib/booth'

  let { state }: { state: BoothState } = $props()
</script>

<div class="result">
  <Confetti opacity={0.5} />
  <div class="print">
    <img src="/media/{state.capture_id}/photo.jpg" alt="" />
  </div>
  <div class="side">
    <div class="scan"><Sticker tilt={-5} size={31} border={5} shadow={[6, 8]}>SCAN TO GET<br />YOUR PHOTO</Sticker></div>
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
    --qr-border: 16px;

    position: absolute;
    inset: 0;
  }

  .print {
    position: absolute;
    left: 4%;
    top: 13%;
    width: 58%;
    rotate: -2deg;
    border: 12px solid #fff;
    background: #000;
    box-shadow: 12px 16px 0 #0009;
  }

  .print img {
    display: block;
    width: 100%;
    aspect-ratio: 16 / 9;
    object-fit: cover;
  }

  .side {
    position: absolute;
    right: 5%;
    top: 10%;
    bottom: 10%;
    width: 27%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 22px;
  }

  .scan {
    align-self: center;
    margin-bottom: calc(var(--qr-border) * -1);
    z-index: 1;
  }

  .qr {
    display: block;
    width: 100%;
    aspect-ratio: 1;
    border: var(--qr-border) solid #fff;
    background: #fff;
  }

  .actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }

  button {
    grid-column: 2;
    padding: 17px 0;
    border-radius: 999px;
    font-size: 23px;
    font-weight: 800;
    transition: transform 80ms ease-out;
  }

  button:active {
    transform: scale(0.95);
  }

  .delete {
    grid-column: 1;
    border: 3px solid #555;
    color: var(--muted);
  }

  .new {
    background: var(--green);
    box-shadow: 0 0 28px #2bd96b55;
    color: #000;
  }
</style>
