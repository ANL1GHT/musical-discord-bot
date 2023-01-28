# musical-discord-bot
Discord bot for music
Это музыкальный дискорд бот, функционал почти полностью соответствует популярным музыкальным ботам(Комментарии к коду и сам функционал написан на русском языке)
У бота реализованы два класса - один для использования проигрывания музыки с ютуба, второй - для проигрывания музыки с вашего компьютера
Также в командную строку после запуска выводятся все данные о боте и его логи(данные об его использовании), и ошибки

Реализация бота в файле bot.py
Запуск бота осуществляется с помощью файла main.py
Данные бота в файле config.py

Чтобы включить код для бота нужно его привязать к боту в дс(его можно создать на портале discord developers)
	и подключить токен бота прописав его в файле в файле config.py в папке cod_bot в строку token

Для функционирования бота нужно установить ffmpeg.exe(https://raw.githubusercontent.com/Raptor123471/DingoLingo/master/ffmpeg.exe)
Также нужна установка дополнительных модулей:
  discord.py
  ffmpeg
  yt-dlp
  youtube-search
Для хранения треков создайте папку tracks

В файле config.py в папке cod_bot указаны токен бота и его префикc,
 если хотим изменить конфигурацию на работу бота с треками не с ютуб а с компьютера, пишем в config.py вместо configuration = 'Bot'
											configuration = 'FileBot'


