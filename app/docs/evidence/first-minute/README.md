# The first minute in Loam

Each column is a separate blank install for Sam. The starter and library were seeded with the installer commands, including real Iris provisioning. Chromium used the app, session broker and Files over loopback. The default Latte theme followed the browser's light or dark setting. The phone viewport was 390 × 844 and the desktop viewport was 1440 × 960.

| Screen | Phone · light | Phone · dark | Desktop · light | Desktop · dark |
| --- | --- | --- | --- | --- |
| First screen | [View](first-screen-phone-light.png) | [View](first-screen-phone-dark.png) | [View](first-screen-desktop-light.png) | [View](first-screen-desktop-dark.png) |
| Hello and Board before use | [View](hello-phone-light.png) | [View](hello-phone-dark.png) | [View](hello-desktop-light.png) | [View](hello-desktop-dark.png) |
| Hello after using Board | [View](board-icon-used-phone-light.png) | [View](board-icon-used-phone-dark.png) | [View](board-icon-used-desktop-light.png) | [View](board-icon-used-desktop-dark.png) |
| Empty board | [View](empty-board-phone-light.png) | [View](empty-board-phone-dark.png) | [View](empty-board-desktop-light.png) | [View](empty-board-desktop-dark.png) |
| Board with a note | [View](filled-board-phone-light.png) | [View](filled-board-phone-dark.png) | [View](filled-board-desktop-light.png) | [View](filled-board-desktop-dark.png) |
| Empty Files folder | [View](empty-files-phone-light.png) | [View](empty-files-phone-dark.png) | [View](empty-files-desktop-light.png) | [View](empty-files-desktop-dark.png) |
| Folder with a note | [View](filled-files-phone-light.png) | [View](filled-files-phone-dark.png) | [View](filled-files-desktop-light.png) | [View](filled-files-desktop-dark.png) |
| Empty attachment folder | [View](empty-picker-phone-light.png) | [View](empty-picker-phone-dark.png) | [View](empty-picker-desktop-light.png) | [View](empty-picker-desktop-dark.png) |
| Attachment folder with a note | [View](filled-picker-phone-light.png) | [View](filled-picker-phone-dark.png) | [View](filled-picker-desktop-light.png) | [View](filled-picker-desktop-dark.png) |
| Empty Apps | [View](empty-apps-phone-light.png) | [View](empty-apps-phone-dark.png) | [View](empty-apps-desktop-light.png) | [View](empty-apps-desktop-dark.png) |
| Apps with a private site | [View](filled-apps-phone-light.png) | [View](filled-apps-phone-dark.png) | [View](filled-apps-desktop-light.png) | [View](filled-apps-desktop-dark.png) |

The phone check read the broker's unread count while the list was on screen. Opening Board dismissed its caption in a second, clean browser context. Settings still had its own caption. The desktop's first screen is the open Hello. [Browser check](../../../test/first-minute-browser.mjs)

The board and Files pairs used writes through the app API. The Apps pair used a private test site's static directory and deployment record in each isolated install. The edge proxy and timers were outside this walk.

## Telling Iris about yourself

A separate blank install used the real Claude Code provider with its sign-in mounted read-only. The tester opened About me in the Files UI between m3 and m4.

Hello `t-d2991ab4` • Claude Code • `claude-fable-5-2`

**Sam · m2**

> I restore old radios on weekends. I learn best from a short checklist and have about half an hour before breakfast. Could you remember those things about me?

**Iris · m3**

> Done. All three are in [[About me]], at the top of Files, in your own words:
>
> - I restore old radios on weekends.
> - I learn best from a short checklist.
> - I have about half an hour before breakfast.
>
> Every agent we make will read that page, so they'll know to keep things short and checklist-shaped for you. Have a look when you get a moment and tell me if I got anything wrong.
>
> One small aside: this chat's board has a few things you can try whenever you like, no rush on any of them.
>
> What radio is on the bench at the moment?

**Sam · m4**

> I opened About me in Files and it sounds like me. My next project is an old tabletop radio. Could I leave you something small to prepare for breakfast tomorrow?

**Iris · m5**

> Glad it sounds like you. That page, and everything else in Files, is yours to keep and edit: plain files, and you can change any word I wrote.
>
> And yes, please do. I wake by myself each night, so I can have something ready and waiting here in this chat before breakfast.
>
> Given what you've told me, one thing I could prepare is a short checklist for your first half hour with the tabletop radio: what to look over and note down before anything gets plugged in or taken apart. If you had something else in mind, say so and I'll do that instead.
>
> If the checklist is the one, what's the make and model, or, if you don't know yet, roughly how old does it look?

**Sam · m6**

> Yes, please leave that short visual checklist here for breakfast. The maker's label is unreadable. Checking the cabinet and taking a few photos is enough for tomorrow.

**Iris · m7**

> It's set. I've written the checklist already and left myself a note to post it here at my nightly wake, which is at 01:00 UTC, so it will be waiting in this chat when you sit down tomorrow.
>
> It covers just what you said: photos first, then a look over the cabinet, sized for half an hour, with the radio staying unplugged. I've included a couple of shots of that unreadable label, since light from the side sometimes brings faded print back.
>
> Enjoy your breakfast, Sam.

### About me after the exchange

```markdown
# About me

What my agents know about me. They add to it as they learn what lasts; it is
mine to read and edit.

- I restore old radios on weekends.
- I learn best from a short checklist.
- I have about half an hour before breakfast.
```

The scratch workspace contained Iris's prepared checklist in `memory/2026-11-22-radio-checklist.md` and her reminder in `nights.md`. The check ended before the scheduled wake.

## Starting with appearance

Hello `t-9d1a4347` • Claude Code • `claude-fable-5-2`

**Sam · m2**

> I'd like to start by changing how this looks. Please switch my desktop to dark mode.

**Iris · m3**

> Done, your desktop is in dark mode now. It should have changed in front of you without a reload.
>
> It will stay dark until you say otherwise. If you'd ever rather it follow the daylight where you are, or your device's own setting, I can switch it to either.
>
> By the way, this chat's board has a few things you can try whenever you like.

**Sam · m4**

> I'd like the Plain theme on desktop, too. Please switch to it and keep dark mode.

**Iris · m5**

> Your desktop is on the Plain theme now, and still in dark mode. I checked both after the switch.
>
> If Plain doesn't sit right once you've looked around, Latte is the other one installed and I can put it back.

### Appearance readback

`GET /api/settings/appearance` returned the following saved values.

```json
{
  "desktop": {
    "theme": "plain",
    "mode": "dark"
  },
  "phone": {
    "theme": "latte",
    "mode": "system"
  }
}
```

In an earlier test container the settings client used its normal broker address while the scratch broker listened on a loopback test port. Iris found the listening service and completed the change. The quoted walk started from another blank install with the scratch broker URL set for the settings client.
