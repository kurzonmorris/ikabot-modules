#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import csv
import os
import sys
import traceback
from ikabot.helpers.gui import banner, enter
from ikabot.helpers.pedirInfo import chooseCity
from ikabot.helpers.getJson import getCity

def spyreport(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    banner()
    
    try:
        # 1. Seleccionar ciudad (Menú en inglés)
        print('Select the city to read spy reports from:')
        ciudad = chooseCity(session)
        
        # Sincronizar contexto
        session.get(f"view=city&cityId={ciudad['id']}", noIndex=True)
        
        # 2. Localizar el Escondite
        city_html = session.get(f"view=city&cityId={ciudad['id']}")
        city_data = getCity(city_html)
        sh_position = next((b['position'] for b in city_data['position'] if b.get('building') == 'safehouse'), None)
        
        if sh_position is None:
            print(f"[DEBUG] [!] No Safehouse found in {ciudad['name']}.")
            enter(); return

        print(f"[DEBUG] Safehouse at pos {sh_position}. Fetching reports...")
        
        # 3. Obtener listado de reportes
        params = {
            'view': 'safehouse', 
            'activeTab': 'tabReports', 
            'cityId': ciudad['id'], 
            'position': str(sh_position), 
            'ajax': '1'
        }
        resp_text = session.post(params=params)
        resp = json.loads(resp_text, strict=False)

        html = ""
        for update in resp:
            if update[0] == "changeView":
                html = update[1][1]
                break
        
        if not html:
            print("[DEBUG] [!] Could not load reports."); enter(); return

        # 4. Procesar bloques y exportar a CSV
        ruta_csv = os.path.join(os.getcwd(), 'espionage_reports.csv')
        with open(ruta_csv, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(['Date', 'Target City', 'Owner', 'Mission', 'Details'])

            # Dividimos el HTML por bloques de mensaje para capturar cabecera y contenido oculto
            blocks = re.findall(r'<tr id="message(\d+)"(.*?)(?=<tr id="message|$)', html, re.DOTALL)
            print(f"[DEBUG] Processing {len(blocks)} reports...")

            for r_id, content_block in blocks:
                # Extraer datos de la cabecera (Fecha, Misión, Objetivo)
                header_part = content_block.split('</tr>')[0]
                fecha = re.search(r'<td class="date".*?>(.*?)</td>', header_part, re.DOTALL).group(1).strip()
                mision = re.search(r'<td colspan="2" class="subject.*?>(.*?)</td>', header_part, re.DOTALL).group(1).strip()
                target_city = re.sub(r'<[^>]+>', '', re.search(r'<td class="targetCity.*?">(.*?)</td>', header_part, re.DOTALL).group(1)).strip()
                target_owner = re.search(r'<td class="targetOwner.*?">(.*?)</td>', header_part, re.DOTALL).group(1).strip()
                
                detalles = ""

                # Caso A: RECURSOS (Depósito)
                if 'resourcesTable' in content_block:
                    recursos = re.findall(r'<td class="count">(.*?)</td>', content_block)
                    if len(recursos) >= 5:
                        # Limpieza de \u00a0 y separadores
                        res = [re.sub(r'[^\d]', '', r) for r in recursos]
                        detalles = f"Wood: {res[0]}, Wine: {res[1]}, Marble: {res[2]}, Crystal: {res[3]}, Sulfur: {res[4]}"
                
                # Caso B: TROPAS / BARCOS (Regimiento)
                elif 'reportTable' in content_block:
                    tables = re.findall(r'<table class="reportTable">.*?</table>', content_block, re.DOTALL)
                    units_found = []
                    for table in tables:
                        titles = re.findall(r'<th title="([^"]+)"', table)
                        # Buscamos cantidades en las celdas 'count' (pueden ser <b>cantidad</b> o '-')
                        counts = re.findall(r'<td class="count">(.*?)</td>', table, re.DOTALL)
                        
                        # Mapeamos títulos (saltando el icono del edificio) con sus cantidades
                        for i in range(min(len(titles)-1, len(counts))):
                            val = re.sub(r'<[^>]+>', '', counts[i]).strip()
                            if val.isdigit() and int(val) > 0:
                                units_found.append(f"{titles[i+1]}: {val}")
                    
                    detalles = ", ".join(units_found) if units_found else "No units detected"

                if not detalles: detalles = "Report read (no table data)"
                writer.writerow([fecha, target_city, target_owner, mision, detalles])

        print(f"\n[SUCCESS] Reports exported to: {ruta_csv}")
        enter()

    except Exception:
        traceback.print_exc()
        enter()
    finally:
        event.set()