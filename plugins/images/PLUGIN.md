---
id: images
name: Images
version: 1.2.0
author: Loam contributors
api: 0
kind: [extension]
description: "Your agents make and change pictures for you, right in the conversation."
screenshots:
  - {src: assets/shots/images-reply.webp, src_dark: assets/shots/images-reply-dark.webp, caption: "Ask for a picture, and it arrives in the reply."}
  - {src: assets/shots/images-full.webp, src_dark: assets/shots/images-full-dark.webp, caption: "Tap it to see it full size. It’s saved in your vault too."}
default_on: false
icon: assets/icon.svg
keys:
  - provider: openai
    why: Makes and changes images with gpt-image when you have an OpenAI key
    optional: true
  - provider: google
    why: Makes and changes images with Gemini when you have a Google key
    optional: true
touches:
  network:
    - host: api.openai.com
      why: Sends the prompt and any source images to OpenAI's image models
    - host: generativelanguage.googleapis.com
      why: Sends the prompt and any source images to Google's Gemini image models
  record: none
  memory: none
  server: false
contributes:
  agent.tool:
    - name: images
      run: python3 tools/images_mcp.py
      lanes: [persona, engineer]
  agent.prompt:
    - lanes: [persona]
      text: "Use generate_image from the images MCP server for all image generation and editing, including on a provider with its own image tools. It saves each image into the vault as an ordinary file. Read the file to see what you made before you show it. To show an image in your reply, put ![a few words](its path) on a line of its own. Up to three show in your message."
  settings.entry:
    - key: folder
      label: Save new images in
      note: A folder in your vault. An agent can still save one somewhere else when you ask.
      default: Images
    - key: backend
      label: Made with
      choices: [auto, openai, google]
      labels: {auto: "Whichever key you have", openai: OpenAI, google: Google Gemini}
      note: With both keys saved under API keys in Connections, OpenAI makes them unless you choose Google.
      default: auto
    - key: openai_model
      label: OpenAI model
      advanced: true
      default: gpt-image-2
    - key: gemini_model
      label: Google model
      advanced: true
      default: gemini-3-pro-image-preview
---

Ask any agent for a picture (a cover for an essay, an illustration for a
story, a mock-up, a photo with the sky changed) and it appears in its reply.
Each one is also saved in your vault as an ordinary image file, so it is
yours like any other.

## What you can do

- **Make a picture from words.** Describe it; your agent draws it.
- **Change a picture you have.** Give it a photo or an image from your
  vault and say what to change.
- **See it where it was made.** Up to three pictures show in the reply; tap
  one to see it full size.
- **Keep them tidy.** New pictures go to your *Images* folder, or wherever
  you ask.

## How to use it

1. Add an OpenAI key or a Google (Gemini) key in **Settings › Connections ›
   API keys**.
2. Turn Images on here to use that key for image generation across providers.
3. Ask an agent: "Draw me a watercolour of the garden in spring."

## What you need, and what leaves your server

A key from OpenAI or Google. Your description, and any picture you ask it
to change, go to that service from your own server, and the pictures are
billed to you by it. Nothing else does.

---

## Technical details

With both keys, OpenAI makes the pictures unless *Made with* says Google.
The models are `gpt-image-2` and `gemini-3-pro-image-preview` unless set under
Advanced. A high-quality OpenAI image costs roughly 35 times a low-quality
one; each call's cost is in the agent's answer. Pictures are saved as PNG,
JPEG or WebP. An agent shows one by writing `![a few words](path)` on a line
of its own. With Images on, agents use the plugin's `generate_image` tool.
Turning Images off restores the provider's own image tools where available.
