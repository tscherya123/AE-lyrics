{
    function SRS_Importer_Script(thisObj) {
        // Створюємо інтерфейс
        var win = (thisObj instanceof Panel) ? thisObj : new Window("palette", "SRT to AE Importer", undefined, {resizeable: true});
        win.orientation = "column";
        win.alignChildren = ["fill", "top"];

        // --- ГРУПА 1: Ввід тексту ---
        var pnlText = win.add("panel", undefined, "Вставте текст з SRT файлу:");
        pnlText.alignChildren = ["fill", "fill"];
        var srtInput = pnlText.add("edittext", [0, 0, 400, 250], "", {multiline: true, scrolling: true});

        // --- ГРУПА 2: Налаштування ---
        var grpSettings = win.add("group");
        grpSettings.orientation = "column";
        grpSettings.alignChildren = ["left", "top"];

        var modeRadio1 = grpSettings.add("radiobutton", undefined, "Один шар (Keyframes on Source Text)");
        var modeRadio2 = grpSettings.add("radiobutton", undefined, "Окремий шар на кожен рядок (Separate Layers)");
        modeRadio1.value = true; // Дефолт

        // --- ГРУПА 3: Кнопки ---
        var btnGenerate = win.add("button", undefined, "Створити субтитри");

        // --- ЛОГІКА ---

        // Допоміжна функція: перетворення часу SRT (00:00:00,000) в секунди для AE
        function parseTime(timeString) {
            if (!timeString) return 0;
            timeString = timeString.replace(',', '.');
            var parts = timeString.split(':');
            var h = parseFloat(parts[0]);
            var m = parseFloat(parts[1]);
            var s = parseFloat(parts[2]);
            return (h * 3600) + (m * 60) + s;
        }

        // Парсер SRT
        function parseSRT(rawData) {
            // Нормалізація рядків
            var lines = rawData.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
            var subtitles = [];
            var currentSub = null;
            
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].replace(/^\s+|\s+$/g, ''); // trim
                
                // Якщо це число (індекс)
                if (line.match(/^\d+$/) && !currentSub) {
                    continue; 
                }
                
                // Якщо це таймінг (00:00:00,000 --> 00:00:00,000)
                if (line.match(/\d{2}:\d{2}:\d{2}[,.]\d{3}\s-->\s\d{2}:\d{2}:\d{2}[,.]\d{3}/)) {
                    var times = line.split('-->');
                    currentSub = {
                        start: parseTime(times[0].replace(/\s/g, '')),
                        end: parseTime(times[1].replace(/\s/g, '')),
                        text: ""
                    };
                    continue;
                }
                
                // Якщо це пустий рядок - кінець блоку
                if (line === "") {
                    if (currentSub) {
                        subtitles.push(currentSub);
                        currentSub = null;
                    }
                } else {
                    // Це текст субтитра
                    if (currentSub) {
                        currentSub.text += (currentSub.text === "" ? "" : "\r") + line;
                    }
                }
            }
            // Додаємо останній, якщо файл не закінчився пустим рядком
            if (currentSub) subtitles.push(currentSub);
            
            return subtitles;
        }

        btnGenerate.onClick = function() {
            var comp = app.project.activeItem;
            
            if (!(comp instanceof CompItem)) {
                alert("Будь ласка, відкрийте композицію.");
                return;
            }

            var selectedLayers = comp.selectedLayers;
            if (selectedLayers.length !== 1 || !(selectedLayers[0] instanceof TextLayer)) {
                alert("УВАГА: Виділіть ОДИН текстовий шар, який буде шаблоном (Template).");
                return;
            }

            var templateLayer = selectedLayers[0];
            var rawSRT = srtInput.text;

            if (rawSRT.length < 10) {
                alert("Вставте SRT текст у поле.");
                return;
            }

            var subs = parseSRT(rawSRT);
            
            app.beginUndoGroup("Generate Subtitles");

            if (modeRadio1.value) {
                // === РЕЖИМ 1: ОДИН ШАР (Source Text Keyframes) ===
                
                // Дублюємо шаблон, щоб не псувати оригінал
                var targetLayer = templateLayer.duplicate();
                targetLayer.name = "Subtitles_Track";
                var textProp = targetLayer.property("Source Text");
                
                // Видаляємо старі ключі, якщо були
                while (textProp.numKeys > 0) {
                    textProp.removeKey(1);
                }

                for (var i = 0; i < subs.length; i++) {
                    var s = subs[i];
                    
                    // Ставимо ключ з текстом на початку
                    textProp.setValueAtTime(s.start, new TextDocument(s.text));
                    
                    // Ставимо пустий ключ в кінці (щоб текст зник)
                    // Якщо наступний субтитр починається одразу, цей ключ може бути зайвим, 
                    // але для надійності краще ставити, щоб були паузи.
                    // Але перевіримо, чи не співпадає кінець цього з початком наступного.
                    
                    var nextStart = (i < subs.length - 1) ? subs[i+1].start : 99999;
                    
                    // Якщо між кінцем цього і початком наступного є дірка хоча б 1 кадр
                    if (nextStart - s.end > comp.frameDuration) {
                         textProp.setValueAtTime(s.end, new TextDocument("")); 
                    }
                }
                
                // Робимо ключі Hold (Square), щоб текст не морфився
                // Хоча для Source Text це і так дефолт, але про всяк випадок
                // В API AE Source Text завжди Hold, тому тут додаткових дій не треба.

            } else {
                // === РЕЖИМ 2: ОКРЕМІ ШАРИ ===
                
                for (var i = 0; i < subs.length; i++) {
                    var s = subs[i];
                    
                    // Дублюємо шаблон
                    var newLayer = templateLayer.duplicate();
                    newLayer.name = "Sub_" + (i + 1);
                    newLayer.inPoint = s.start;
                    newLayer.outPoint = s.end;
                    
                    // Встановлюємо текст
                    var textDoc = newLayer.property("Source Text").value;
                    textDoc.text = s.text;
                    newLayer.property("Source Text").setValue(textDoc);
                    
                    // Переміщуємо на гору (опціонально)
                    newLayer.moveToBeginning();
                }
            }
            
            // Ховаємо оригінальний шаблон
            templateLayer.enabled = false;
            templateLayer.label = 0; // None label
            templateLayer.name = "Template (Hidden)";

            app.endUndoGroup();
        }

        win.layout.layout(true);
        return win;
    }

    var scriptPal = SRS_Importer_Script(this);
    if (scriptPal instanceof Window) scriptPal.show();
}