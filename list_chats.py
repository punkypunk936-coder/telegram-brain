import asyncio
from tgbrain import settings, client
async def main():
    s=settings(); tg=client(s); await tg.start(phone=s.phone)
    print('\nID\tTYPE\tTITLE\n'+'-'*90)
    async for d in tg.iter_dialogs(): print(f'{d.id}\t{d.entity.__class__.__name__}\t{d.name}')
    await tg.disconnect()
asyncio.run(main())
